"""async 工具回合循环（spec §3.3）：流式 LLM + 并行工具 + 事件发射。"""
import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any

from fieldwork_agent.kernel.guidance.rule_replies import rule_based_reply
from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.registry import build_tool_schemas
from fieldwork_agent.kernel.sources.flyai import RecordingClient
from fieldwork_agent.runtime.doc_args import merge_doc_args
from fieldwork_agent.runtime.loop import (
    _assistant_message,
    _budget_skip_content,
    _coerce_args,
    _error_dict,
    _tool_content,
)
from fieldwork_agent.runtime.turn_buffer import TurnPatchBuffer
from fieldwork_agent.web_host.agent.context import build_messages
from fieldwork_agent.web_host.agent.dispatch import build_dispatch_fn
from fieldwork_agent.web_host.agent.state_tools import build_state_tools
from fieldwork_agent.web_host.turn_manager import TurnManager

# 无 LLM 降级提示：web 宿主无启动横幅渠道，提示附在回复末尾（CLI 宿主用启动横幅呈现）
_DEGRADED_NOTE = ("\n\n（提示：当前未配置 DashScope API Key，处于降级模式，"
                  "以上为固定引导文案；配置后可启用智能对话。）")

# 快捷选项标记：LLM 在枚举型提问末尾输出 [[options]]甲|乙|丙[[/options]]，
# 轮末剥离标记、选项入消息 meta 与 turn_done payload（渐进增强：无标记则无选项）
_OPTIONS_TAG_RE = re.compile(r"\[\[options\]\](.*?)\[\[/options\]\]", re.S)


def _extract_quick_replies(reply: str) -> tuple[str, list[str]]:
    """剥离尾部 [[options]] 标记；返回 (干净正文, 选项列表)。

    选项按 | 切分、strip、滤空，最多取 4 个，每项截断 20 字；
    标记缺失或解析不出有效选项 → (原文, [])。
    """
    match = _OPTIONS_TAG_RE.search(reply)
    if not match:
        return reply, []
    options = [opt.strip()[:20] for opt in match.group(1).split("|")]
    options = [opt for opt in options if opt][:4]
    if not options:
        return reply, []
    return reply[:match.start()].rstrip(), options


# 中文全角引号归一（问题3）：LLM 输出的成对半角双引号在内容含中文时
# 归一为全角“”；纯西文内容保持半角；代码块（行首 ``` 围栏内）不动。
# 围栏判定用行首配对（终审 B4）：split("```") 会把行内 ``` 误当围栏，
# 奇偶错位后把真代码块内的引号误转全角。
_HALF_QUOTED_RE = re.compile(r'"([^"\n]*)"')
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_FENCE_LINE_RE = re.compile(r"(?m)^[ \t]*```[^\n]*$")


def _quote_outside_code(seg: str) -> str:
    """正文段的半角双引号归一：成对、同行、内容含中文才转全角。"""
    return _HALF_QUOTED_RE.sub(
        lambda m: f"“{m.group(1)}”" if _CJK_CHAR_RE.search(m.group(1))
        else m.group(0),
        seg)


def _normalize_cjk_quotes(text: str) -> str:
    """成对、同行、无嵌套的半角双引号对：内容含中文才转全角，否则原样。

    行首 ``` 围栏按出现顺序配对翻转 in_code，行内 ``` 不构成围栏；
    未闭合围栏后的尾部按代码块内处理（宁漏转正文，不误转代码）。
    """
    out: list[str] = []
    pos = 0
    in_code = False
    for fence in _FENCE_LINE_RE.finditer(text):
        seg = text[pos:fence.start()]
        out.append(seg if in_code else _quote_outside_code(seg))
        out.append(fence.group(0))  # 围栏行原样
        in_code = not in_code
        pos = fence.end()
    tail = text[pos:]
    out.append(tail if in_code else _quote_outside_code(tail))
    return "".join(out)


async def run_turn(repo, pid: str, turn_id: str, user_text: str,
                   tm: TurnManager, llm: Any, flyai_client: Any, *,
                   amap_client: Any = None,
                   max_iters: int = 8, max_tool_calls: int = 20,
                   max_wall_seconds: float = 60.0) -> None:
    """执行完整回合：流式 LLM + 并行工具 + 事件发射 + 阶段推进 + 持久化。"""
    recorder = RecordingClient(flyai_client) if flyai_client is not None else None
    # N5：amap 同款双包装（spec §5.1 快照链对高德原语同样成立）——聚合层
    # 经 dispatch 拿到的是 recorder 透传视图，实际调用被逐次记录
    amap_recorder = (RecordingClient(amap_client)
                     if amap_client is not None else None)
    live_refs: dict[str, str] = {}
    snapshot_ids: list[str] = []
    drained = 0
    drained_amap = 0
    call_args_map: dict[str, dict] = {}
    # N04：文档工具生成时的仓储依赖视图（ctx 路 provenance）——落盘指纹据此
    # 而非保存时推测；正常轮与保存时仓储一致，仅并发外部修改时有别。
    doc_ctx_provenance: dict[str, dict] = {}
    dispatch = build_dispatch_fn(recorder if recorder is not None else flyai_client,
                                 llm,
                                 amap_recorder if amap_recorder is not None
                                 else amap_client)
    tools = build_tool_schemas()
    # F05：回合级 patch 暂存——状态工具与时窗回写先进 buffer，轮末正常结束
    # commit 落盘、取消 discard，项目状态不受残缺回合影响
    buffer = TurnPatchBuffer(repo, pid)
    state_schemas, state_handlers = build_state_tools(repo, pid, buffer=buffer)
    tools = tools + state_schemas
    phase = repo.get_phase(pid)

    # R4：用户消息即时持久化（F09 修正：先 build 后 append——
    # build_messages 读 history 后自行 append 当前消息，顺序颠倒会双发）
    messages = build_messages(repo, pid, user_text, phase)
    repo.append_message(pid, "user", user_text)

    iterations = 0
    total_calls = 0
    start = time.monotonic()
    accumulated_reply = ""
    stream_prefix = ""          # 前导语文本（已流出，重问后仍拼回持久化正文）
    preamble_retried = False    # 只重问一次，防无限循环
    incomplete = False          # 回合未收敛出正文 → turn_done.incomplete
    has_final_answer = False
    finish_reason = ""
    usage_total: dict = {}
    call_name_map: dict[str, str] = {}

    try:
        while True:
            handle = tm.get_turn(pid, turn_id)
            # 项目删除竞态（审查 1.2）：purge 已连同订阅/磁盘/内存清除回合，
            # 无处发事件也无处持久化，静默退出（finish 同样无处可写）
            if handle is None:
                return
            if handle.cancel_event.is_set():
                break
            if iterations >= max_iters or total_calls >= max_tool_calls:
                break
            if time.monotonic() - start > max_wall_seconds:
                break

            # 无 LLM（未配置 DashScope key）降级：规则文案回复后退出，走统一轮末
            if llm is None:
                reply = rule_based_reply(phase) + _DEGRADED_NOTE
                tm.append_event(pid, turn_id, "message_delta", {"text": reply})
                accumulated_reply = reply
                break

            # 流式 LLM
            stream = llm.chat_stream(messages, tools=tools, slot="interview")
            iterations += 1
            final_resp = None
            async for chunk in _async_iter(stream):
                if tm.get_turn(pid, turn_id) is None:
                    return  # 项目删除竞态：停止消费已无消费者的流
                if "delta" in chunk and chunk["delta"]:
                    tm.append_event(pid, turn_id, "message_delta",
                                    {"text": chunk["delta"]})
                elif "content" in chunk:
                    final_resp = chunk

            if final_resp is None:
                break
            # LLM 错误帧（key 无效/限流等，kernel.llm 契约：HTTP 非 200 返零值+error 键）：
            # 如实发 error + failed 终态。回归：此前 content="" 被当正常终止帧，
            # 错误静默吞掉、产出空 turn_done（配错 key 表现为「没反应」）。
            llm_error = final_resp.get("error")
            if llm_error:
                tm.append_event(pid, turn_id, "error",
                                {"code": "llm_error", "message": str(llm_error),
                                 "hint": "请检查百炼 API Key 是否有效"})
                tm.finish(pid, turn_id, "failed")
                return
            raw = final_resp.get("content", "") or ""
            if raw:
                accumulated_reply = stream_prefix + raw
            usage_total = final_resp.get("usage", {}) or usage_total
            tool_calls = final_resp.get("tool_calls")
            if not tool_calls:
                finish_reason = str(final_resp.get("finish_reason") or "")
                answer = raw.strip()
                if (answer and finish_reason != "length" and not preamble_retried
                        and _looks_like_preamble(raw)):
                    # 前导语（"我这就去查…"/"先问最实际的："）不是最终回答：文本
                    # 已流出，记入前缀；带工具重问一次让它真正动手。length 不走此
                    # 分支（截断续写仍然 tools=None，保持既有语义）。
                    preamble_retried = True
                    stream_prefix += raw
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "system", "content": _PREAMBLE_NUDGE})
                    continue
                if answer and (finish_reason == "length" or not _looks_like_preamble(raw)):
                    has_final_answer = True
                break

            # 并行工具执行（F07：批前截断——越过额度的调用不执行，直接回填
            # budget_exceeded 的 role=tool 消息与事件，协议上每个 id 均有回填）
            messages.append(_assistant_message(final_resp))
            remaining = max_tool_calls - total_calls
            executed, skipped = tool_calls[:remaining], tool_calls[remaining:]
            for call in skipped:
                call_id = call.get("id", "")
                name = call.get("name", "")
                err = {"code": "budget_exceeded",
                       "message": "本回合工具调用额度已用尽，该调用未执行"}
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": _budget_skip_content()})
                tm.append_event(pid, turn_id, "tool_completed",
                                {"call_id": call_id, "ok": False, "data": None,
                                 "degraded": None, "error": err})
            # N04：同批工具按依赖顺序执行——状态写工具（update_research_context
            # 等，name 在 state_handlers）先执行并 stage 进 buffer；随后才组装
            # 其余工具（含文档工具）的入参并并发执行，使 merge_doc_args 读到
            # 已 stage 的新 ctx（修复同批写→文档时文档拿到旧值、正文与依赖
            # 指纹错配）。独立只读搜索与文档工具在第二阶段仍并发。
            def _prepare(call):
                name = call.get("name", "")
                call_id = call.get("id", "")
                args = _coerce_args(call.get("arguments"))
                # 终审 M1：文档工具入参合并读"仓储+暂存"合并视图——同轮先
                # update_research_context 再调文档工具时立即看到新值（与
                # get_project_brief 的 staged_view 语义一致）
                ctx_view = buffer.staged_view()
                args = merge_doc_args(repo, pid, name, args, ctx_view=ctx_view)
                call_name_map[call_id] = name
                call_args_map[call_id] = args
                # N04：compose_proposal 落盘指纹的 ctx 路依据＝生成时仓储依赖视图
                # （非模型合并后的 ctx，也非保存时推测）
                if name == "compose_proposal":
                    doc_ctx_provenance[call_id] = dict(ctx_view)
                tm.append_event(pid, turn_id, "tool_started",
                                {"call_id": call_id, "tool": name,
                                 "args": args, "purpose": ""})
                return _exec_tool(dispatch, name, args, call_id, state_handlers)

            write_calls = [c for c in executed if c.get("name", "") in state_handlers]
            rest_calls = [c for c in executed if c.get("name", "") not in state_handlers]
            results = []
            if write_calls:
                # 第一阶段：依赖写/读状态工具先落地（stage 进 buffer）
                results.extend(await asyncio.gather(
                    *(_prepare(c) for c in write_calls)))
            if rest_calls:
                # 第二阶段：文档工具此刻组装入参已能读到新 ctx；与只读搜索并发
                results.extend(await asyncio.gather(
                    *(_prepare(c) for c in rest_calls)))
            total_calls += len(executed)

            for call_id, name, env in results:
                data = env.data
                if hasattr(data, "model_dump"):
                    data = data.model_dump(mode="json")
                tm.append_event(pid, turn_id, "tool_completed",
                                {"call_id": call_id, "ok": env.ok,
                                 "data": data, "degraded": env.degraded,
                                 "error": _error_dict(env)})
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": _tool_content(name, env)})
                if name == "analyze_fieldwork_window" and env.ok:
                    dest = str(call_args_map.get(call_id, {}).get("destination", ""))
                    cid = repo.cache_search(pid, f"time_window::{dest}",
                                            "analyze_fieldwork_window", data)
                    snapshot_ids.append(cid)
                    # F03：推荐时窗与用户约束分离——写 candidate_windows
                    # （候选，未经用户确认），time_window 只留用户确认值
                    wins = [w for w in (data.get("windows") or [])
                            if w.get("start") and w.get("end")][:3]
                    if wins:
                        payload = [
                            {"start": w["start"], "end": w["end"],
                             "reason": (w.get("reasons") or [""])[0]}
                            for w in wins
                        ]
                        # F05：经回合级暂存，取消回合不落残缺候选
                        buffer.stage({"candidate_windows": payload})
            # 快照链（R08 §6）：搜索即时落盘，并按 _live_key 归并 规格键→快照引用，
            # 轮末 _persist_tool_results 统一替换聚合产出中的规格键
            drained = _drain_snapshots(repo, pid, recorder, snapshot_ids,
                                       live_refs, drained)
            # 双 recorder 各自游标（合并视图会错位）；snapshot_ids/live_refs
            # 共享：turn_done 汇总全部快照，轮末替换按键前缀天然不冲突
            drained_amap = _drain_snapshots(repo, pid, amap_recorder,
                                            snapshot_ids, live_refs,
                                            drained_amap, label="amap")

    except Exception as exc:
        tm.append_event(pid, turn_id, "error",
                        {"code": "internal_error", "message": str(exc), "hint": ""})
        tm.finish(pid, turn_id, "failed")
        return

    # 截断续写防御（生产实录 field_20261018_202833 回合4：回复停在
    # 「先问最实际的：」而 turn 照常 completed）：无 tool_calls 终止且
    # finish_reason=length，或非空正文以未完标点/开引号结尾（服务端异常终止）
    # 时，追加 system 续写指令再流式一次拼接（不带工具，防续写轮两调工具）；
    # 只续一次，续写失败或仍空则保留原文——已流出的内容绝不丢。
    handle = tm.get_turn(pid, turn_id)
    if handle is None:
        return  # 项目删除竞态：续写与轮末的写入目标均已消失
    if (llm is not None and accumulated_reply
            and not handle.cancel_event.is_set()
            and _looks_truncated(accumulated_reply, finish_reason)):
        messages.append({"role": "system", "content":
                         "你的上一条回复在句中被截断了，已输出内容的末尾如下：\n"
                         + accumulated_reply[-300:]
                         + "\n请紧接着上述末尾继续写完剩余内容：直接续写正文，"
                           "不要重复已写出的内容，不要重新开头，写完即止。"})
        cont_deltas = ""  # 续写流已流出增量：终帧空/报错/流异常三路兜底（终审 B3）
        try:
            stream = llm.chat_stream(messages, tools=None, slot="interview")
            iterations += 1
            final_resp = None
            async for chunk in _async_iter(stream):
                if tm.get_turn(pid, turn_id) is None:
                    return  # 项目删除竞态：同主流，停止消费与发射
                if "delta" in chunk and chunk["delta"]:
                    cont_deltas += chunk["delta"]
                    tm.append_event(pid, turn_id, "message_delta",
                                    {"text": chunk["delta"]})
                elif "content" in chunk:
                    final_resp = chunk
            if final_resp and not final_resp.get("error"):
                cont = final_resp.get("content", "") or ""
                # 终帧正文空（只发 delta 不发终帧正文的实现）同样以增量兜底
                accumulated_reply += cont or cont_deltas
                finish_reason = (str(final_resp.get("finish_reason") or "")
                                 or finish_reason)
                usage_total = final_resp.get("usage", {}) or usage_total
            else:
                # 无终帧/终帧报错：已流出的续写增量照样拼入，绝不丢
                accumulated_reply += cont_deltas
        except Exception:
            # 流中断：同上兜底（终审 B3），不丢已流出的内容
            accumulated_reply += cont_deltas

    # 最终回答收敛（对齐 CLI runtime/loop.py 的触顶收敛；F06 收敛条件整体
    # 替换为 not has_final_answer）：主循环退出但没拿到最终回答（预算触顶
    # 裸退出、思考模型「只想不写」、或只输出了前导语就触顶）时不让回合静默
    # 结束——追加 system 收敛指令再取一次最终回答（不带工具，防继续调用）；
    # 仍空则按空/非空走三分支兜底。cancel 已取消的回合不做兜底（终态 cancelled）。
    handle = tm.get_turn(pid, turn_id)
    if handle is None:
        return  # 项目删除竞态：收敛与轮末的写入目标均已消失
    if (llm is not None and not has_final_answer
            and not handle.cancel_event.is_set()):
        limit_hit = (iterations >= max_iters or total_calls >= max_tool_calls
                     or time.monotonic() - start > max_wall_seconds)
        reason = ("本回合已达到处理上限。" if limit_hit
                  else "上一轮未生成文字回复。")
        messages.append({"role": "system", "content":
                         reason + "请基于以上对话与工具结果，用中文给出简明的"
                                 "阶段性回答；不要再调用工具。"})
        try:
            stream = llm.chat_stream(messages, tools=None, slot="interview")
            iterations += 1
            final_resp = None
            async for chunk in _async_iter(stream):
                if tm.get_turn(pid, turn_id) is None:
                    return  # 项目删除竞态：同主流，停止消费与发射
                if "delta" in chunk and chunk["delta"]:
                    tm.append_event(pid, turn_id, "message_delta",
                                    {"text": chunk["delta"]})
                elif "content" in chunk:
                    final_resp = chunk
            if final_resp and not final_resp.get("error"):
                if (final_resp.get("content") or "").strip():
                    accumulated_reply = stream_prefix + final_resp["content"]
                    has_final_answer = True  # 收敛成功即最终回答，三分支不再追加提示
                usage_total = final_resp.get("usage", {}) or usage_total
        except Exception:
            accumulated_reply = ""  # 收敛重试失败不改变回合结果，走兑底文案
        if not accumulated_reply:
            incomplete = True
            fallback = ("（已达本回合处理上限，未能基于现有信息生成完整回答；"
                        "请继续提问以接着现有材料推进。）" if limit_hit else
                        "（本次未生成文字回复；请继续提问或稍后重试。）")
            tm.append_event(pid, turn_id, "message_delta", {"text": fallback})
            accumulated_reply = fallback
        elif not has_final_answer:
            incomplete = True
            # 前导语 + 未获最终回答：保留过程说明，追加提示
            note = ("\n\n（以上为过程说明；已达本回合处理上限，未能生成完整总结，"
                    "请继续提问以接着现有材料推进。）" if limit_hit else
                    "\n\n（以上为过程说明；本次未生成文字总结，请继续提问。）")
            tm.append_event(pid, turn_id, "message_delta", {"text": note})
            accumulated_reply = accumulated_reply + note

    # 轮末：助手消息持久化 + 工具结果落盘 + 抽取写回 + 自动命名（任务1）+ 阶段推进
    # 审计二 R05：任一步异常 → error 事件 + failed 终态，绝不悬挂在 running
    # P1-4：取消即中止本轮全部状态变更——残缺回复/工具产物/抽取/阶段推进
    # 均不落地，直接走 cancelled 终态（轮末块同步无 await，取消不会中途插入）
    handle = tm.get_turn(pid, turn_id)
    if handle is None:
        return  # 项目删除竞态：防收敛块之后、轮末之前的残余窗口
    cancelled = handle.cancel_event.is_set()
    quick_replies: list[str] = []
    if not cancelled and accumulated_reply:
        accumulated_reply, quick_replies = _extract_quick_replies(accumulated_reply)
        accumulated_reply = _normalize_cjk_quotes(accumulated_reply)
    if not cancelled:
        try:
            if accumulated_reply:
                repo.append_message(pid, "assistant", accumulated_reply,
                                    {"tool_calls": total_calls,
                                     "finish_reason": finish_reason,
                                     **({"quick_replies": quick_replies}
                                        if quick_replies else {})})
            buffer.commit()  # F05：正常轮末提交暂存 patch（取消/失败路径不落盘）
            _persist_tool_results(repo, pid, tm, turn_id, call_name_map, live_refs,
                                  doc_ctx_provenance)
            _apply_extractions(repo, pid, user_text)
            _maybe_autoname(repo, pid, llm)
            transition = check_and_advance(repo, pid)
            if transition:
                tm.append_event(pid, turn_id, "phase_transition", transition)
        except Exception as exc:
            tm.append_event(pid, turn_id, "error",
                            {"code": "internal_error",
                             "message": f"轮末持久化失败: {exc}", "hint": ""})
            tm.finish(pid, turn_id, "failed")
            return

    # 终态
    if cancelled:
        buffer.discard()  # F05：取消丢弃本轮暂存
        tm.finish(pid, turn_id, "cancelled")
    else:
        tm.append_event(pid, turn_id, "turn_done",
                        {"reply_id": turn_id, "usage": usage_total,
                         "snapshot_ids": snapshot_ids,
                         "quick_replies": quick_replies,
                         "finish_reason": finish_reason,
                         "incomplete": incomplete})
        tm.finish(pid, turn_id, "completed")


# 未完标点/截断算式：非空正文以其结尾几乎必为句中截断（服务端异常终止），
# 触发一次续写兑底；句号/问号/叹号等完整结尾不在此列。
# 冒号移出本表：以冒号结尾多为「前导语」，走带工具重问（见 _looks_like_preamble）。
# 引号/省略号移出本表（终审 B2）：闭合引号是完整结构、省略号是语气收尾——
# 误触发续写会在完整句后拼出续貂文本；漏判代价仅保留原文，可接受。
_INCOMPLETE_TAIL_CHARS = '，,、；;—×÷≈+-='

# 前导语尾字符 + 承诺词：无 tool_calls 且命中任一 → 不是最终回答
_PREAMBLE_TAIL_CHARS = "：:，,、；;—…"
_PROMISE_MARKERS = ("我这就", "马上", "我先", "接下来", "下面", "让我", "这就去",
                    "先来", "我来", "实测", "查一下", "查一查")

_PREAMBLE_NUDGE = ("你上一条回复只是前导语（说明接下来要做什么），尚未给出结果。"
                   "请立即调用合适的工具获取真实数据，拿到结果后再给出完整回答；"
                   "不要再输出「我这就去查」这类前导语。")


def _looks_like_preamble(text: str) -> bool:
    """前导语判定：以未完标点收尾，或短句且含承诺词、但未以句末标点收尾。"""
    body = text.strip()
    if not body:
        return False
    if body[-1] in _PREAMBLE_TAIL_CHARS:
        return True
    return (len(body) <= 120 and body[-1] not in "。！？"
            and any(marker in body for marker in _PROMISE_MARKERS))


def _looks_truncated(reply: str, finish_reason: str) -> bool:
    """终止帧是否截断：length 触顶，或正文尾部落在未完标点/截断算式上。"""
    if finish_reason == "length":
        return True
    tail = reply.rstrip()
    return bool(tail) and tail[-1] in _INCOMPLETE_TAIL_CHARS


_SENTINEL = object()


def _next_or_sentinel(it):
    """next() 包装：StopIteration 转哨兵（StopIteration 不可 raise 进 Future）。"""
    try:
        return next(it)
    except StopIteration:
        return _SENTINEL


async def _async_iter(sync_gen):
    """同步 generator → async iterator（经线程池，不阻塞 event loop）。"""
    it = iter(sync_gen)
    while True:
        val = await asyncio.to_thread(_next_or_sentinel, it)
        if val is _SENTINEL:
            break
        yield val


async def _exec_tool(dispatch, name, args, call_id, local_handlers):
    if name in local_handlers:
        # 能力工具走 dispatch（args_model 校验折 invalid_arguments）；
        # state tools 无 args_model，此前 ``(**args)`` 缺参/多参的 TypeError
        # 直接炸穿 gather → 整轮 error+failed，已流出的正文全部丢失且 LLM
        # 无从自救（轮 4 实测：update_research_context 漏 patch）。对齐
        # dispatch 语义：签名不匹配折 invalid_arguments（LLM 可补参重试），
        # 其余异常折 internal_error，均不外抛。
        try:
            env = local_handlers[name](**args)
        except TypeError as exc:
            env = Envelope(ok=False, error=ToolError(
                code="invalid_arguments", message=f"参数调用失败：{exc}",
                hint="请按工具 schema 补齐必填参数（去掉未知参数）后重试"))
        except Exception as exc:
            env = Envelope(ok=False, error=ToolError(
                code="internal_error", message=str(exc)))
    else:
        env = await asyncio.to_thread(dispatch, name, args)
    return (call_id, name, env)



def _live_key(method: str, kwargs: dict) -> str:
    """从 recorder 捕获的调用参数生成规格键——与 kernel 侧 live_*_ref 逐字符一致。

    amap 分支（N5，kernel/aggregation/amap_refs.py 契约）：副参不进键
    （city/radius），date 缺省留空段；非契约方法（weather 等）无键。
    """
    if method in ("search_flights", "search_trains"):
        origin = kwargs.get("origin") or ""
        dest = kwargs.get("destination") or ""
        dep = kwargs.get("dep_date") or ""
        if not (origin and dest and dep):
            return ""
        return f"@live:{method}:{origin}>{dest}|{dep}"
    if method == "search_hotels":
        dest = kwargs.get("dest_name") or kwargs.get("city_name") or ""
        if not dest:
            return ""
        ref = (f"@live:search_hotels:{dest}|{kwargs.get('check_in') or ''}"
               f"|{kwargs.get('check_out') or ''}")
        types = kwargs.get("hotel_types") or ""
        return f"{ref}|{types}" if types else ref
    if method == "geocode":
        address = kwargs.get("address") or ""
        return f"@live:amap_geocode:{address}" if address else ""
    if method == "around_search":
        keywords = kwargs.get("keywords") or ""
        location = kwargs.get("location") or ""
        return (f"@live:amap_around_search:{keywords}|{location}"
                if keywords and location else "")
    if method == "direction_transit":
        origin = kwargs.get("origin") or ""
        dest = kwargs.get("destination") or ""
        if not (origin and dest):
            return ""
        return (f"@live:amap_direction_transit:{origin}>{dest}"
                f"|{kwargs.get('date') or ''}")
    return ""


def _resolve_live_refs(obj, live_refs: dict[str, str]):
    """递归把 obj 中的 @live: 规格键替换为真实快照引用（无匹配则原样保留）。

    字典精确匹配（D6）：键体可为任意字符（含全角括号/空白地名），
    逐键子串替换；按键长降序避免前缀键截断（如 hotel_types 尾段键是其裸键前缀）。
    """
    if isinstance(obj, dict):
        return {k: _resolve_live_refs(v, live_refs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_live_refs(v, live_refs) for v in obj]
    if isinstance(obj, str):
        for key in sorted(live_refs, key=len, reverse=True):
            if key in obj:
                obj = obj.replace(key, live_refs[key])
        return obj
    return obj


def _persist_tool_results(repo, pid, tm, turn_id, call_name_map, live_refs,
                          doc_ctx_provenance=None):
    """轮末把成功工具结果落盘；数据先经 _resolve_live_refs 替换规格键（R08）。

    N04：``doc_ctx_provenance``（call_id → 生成时仓储依赖视图）供 compose_proposal
    落盘时作为 provenance_ctx 传给 save_proposal，使依赖指纹忠于生成时的
    仓储依赖版本而非保存时推测（并发外部修改时能正确判失效）。
    """
    doc_ctx_provenance = doc_ctx_provenance or {}
    events = tm.replay(pid, turn_id, after_seq=0)
    for ev in events:
        if ev.event != "tool_completed" or not ev.data.get("ok"):
            continue
        call_id = ev.data.get("call_id", "")
        name = call_name_map.get(call_id, "")
        data = ev.data.get("data")
        if not name or data is None:
            continue
        data_dict = data if isinstance(data, dict) else (
            data.model_dump(mode="json") if hasattr(data, "model_dump") else {"value": data})
        data_dict = _resolve_live_refs(data_dict, live_refs)
        if name == "profile_field_site":
            repo.save_field_site(pid, {"name": data_dict.get("location", ""),
                                       "confirmed": False, "profile": data_dict})
        elif name == "compose_itinerary":
            dests = ",".join(data_dict.get("destinations", []) or [])
            repo.cache_search(pid, f"itinerary::{dests}", "compose_itinerary", data_dict)
        elif name == "compose_proposal":
            markdown = data_dict.get("markdown", "")
            if markdown:
                # N04：用生成时仓储依赖视图作为指纹依据（provenance）
                repo.save_proposal(
                    pid, markdown,
                    kind=data_dict.get("kind") or "proposal",
                    version=data_dict.get("version") or "draft",
                    provenance_ctx=doc_ctx_provenance.get(call_id))
        elif name == "compose_interview_guide":
            # guide 返回 {类型: 正文} → 拼单文档落盘
            parts = ["# 访谈提纲", ""]
            for itype, body in (data_dict or {}).items():
                if not str(body).strip():
                    continue
                parts += [f"## {itype}", "", str(body), ""]
            if len(parts) > 2:
                repo.save_proposal(pid, "\n".join(parts),
                                   kind="interview_guide", version="draft")


def check_and_advance(repo, pid) -> dict | None:
    """确定性阶段推进。公共：供 web_host.api 复用。"""
    from fieldwork_agent.kernel.guidance import phases
    from fieldwork_agent.kernel.models import FieldSiteRecord, InformantRecord, ResearchContext
    state = repo.get_state(pid)
    ctx = ResearchContext.model_validate(state.get("research_context") or {})
    sites = [FieldSiteRecord.model_validate(s) for s in state.get("field_sites") or []]
    informants = [InformantRecord.model_validate(i) for i in state.get("informants") or []]
    has_itinerary = any(c.get("tool") == "compose_itinerary"
                        for c in repo.list_cached_searches(pid))
    confirmed = (bool(state.get("proposal_confirmed"))
                 and not repo.is_proposal_confirmation_stale(pid))
    result = phases.check_advance(
        state["phase"], ctx, sites, informants,
        has_itinerary, bool(state.get("proposals")), confirmed)
    if result.can_advance and result.to_phase:
        old = state["phase"]
        repo.set_phase(pid, result.to_phase)
        return {"from": old, "to": result.to_phase}
    return None


def _apply_extractions(repo, pid, user_text):
    """轮末确定性抽取（F03）：对话中的研究信息写回结构化状态。

    复用 CLI 同款 apply_message_extractions（增量语义：已填字段不覆盖、
    extras.* 点号键并入旧 extras 合并落盘）。
    """
    from fieldwork_agent.kernel.guidance.extract import apply_message_extractions
    ctx = repo.get_research_context(pid)
    found = apply_message_extractions(user_text, ctx)
    if not found:
        return
    merged = dict(ctx)
    extras = dict(merged.get("extras") or {})
    for key, value in found.items():
        if key.startswith("extras."):
            extras[key.split(".", 1)[1]] = value
        else:
            merged[key] = value
    if extras:
        merged["extras"] = extras
    repo.update_research_context(pid, merged)


def _maybe_autoname(repo, pid, llm):
    """任务1：agent 自动命名（best-effort）——用户未手动命名（title 空）时，
    依最近对话生成 ≤30 字中文项目名；LLM 不可用走 topic/首条消息兑底。

    任一步失败（含 LLM 返回空）静默返回，下回合再试；title 已非空直接返回
    （用户手动命名后永不覆盖）。写入走 update_research_context 浅合并，
    仅覆盖 title 键。
    """
    try:
        ctx = repo.get_research_context(pid)
        if str(ctx.get("title") or "").strip():
            return
        history = repo.get_messages(pid, limit=10)
        convo = [m for m in history if m.get("role") in ("user", "assistant")]
        if llm is None:
            # 降级模式兑底：topic 前 30 字，否则首条 user 消息（去换行）前 30 字
            topic = str(ctx.get("topic") or "").strip()
            if topic:
                name = topic[:30]
            else:
                first_user = next((str(m.get("content") or "") for m in convo
                                   if m.get("role") == "user"), "")
                name = first_user.replace("\n", " ").strip()[:30]
        else:
            excerpt = "\n".join(
                f"{m['role']}: {str(m.get('content') or '')[:200]}" for m in convo)
            if not excerpt.strip():
                return
            resp = llm.chat(
                [{"role": "system",
                  "content": "为一项田野调研项目起一个简短中文名（不超过30字，"
                             "不用引号、书名号或任何前后缀），只输出名字本身。"},
                 {"role": "user", "content": excerpt}],
                tools=None, slot="extract")
            lines = str((resp or {}).get("content") or "").splitlines()
            name = lines[0].strip().strip('"“”‘’《》').strip()[:30] if lines else ""
        if not name.strip():
            return
        repo.update_research_context(pid, {"title": name})
    except Exception:
        return   # best-effort：失败不影响回合流程，下回合再试


# amap client 方法形参序（N5）：RecordingClient 记录的位置参数按此并入
# 参数视图——geocode 在 last_mile 为位置传参、site_profile 为关键字传参，
# 两种形态都归一到同名视图，供快照 query 与 _live_key 取参
_AMAP_PARAMS = {
    "geocode": ("address", "city"),
    "regeocode": ("location",),
    "text_search": ("keywords", "city", "page_size"),
    "around_search": ("keywords", "location", "radius", "page_size"),
    "direction_transit": ("origin", "destination", "city", "cityd", "date"),
    "weather": ("city", "extensions"),
}


def _drain_snapshots(repo, pid, recorder, snapshot_ids, live_refs, start: int,
                      label: str = "flyai") -> int:
    """消费 recorder.calls[start:] 的新增成功搜索，落盘为快照证据（R08 §6）。

    返回新游标（幂等，同一调用不会重复落盘）。flyai 路径仅记录带目的地
    语义的调用（destination/dest_name/city_name 任一命中）；amap 路径
    （label="amap"）六原语全为地理查询，全部落盘且快照 tool 名带
    ``amap_`` 前缀区分来源，位置参数按 ``_AMAP_PARAMS`` 并入参数视图。
    query 为可读的 ``工具名 {参数 JSON}``；analyze_fieldwork_window 内部的
    多日期采样同样经 recorder 被捕获——它们正是价格曲线的原始依据。
    每次落盘同时按 ``_live_key`` 归并 规格键→快照引用（后写覆盖：同一键
    多次命中取最新），轮末 ``_persist_tool_results`` 据此替换聚合产出中
    的规格键；文案前缀随 label（flyai/amap 快照）。
    recorder 为 None（宿主未注入对应客户端）时原样返回游标。
    """
    if recorder is None:
        return start
    for method, sig, env in recorder.calls[start:]:
        if not getattr(env, "ok", False):
            continue
        if label == "amap":
            view = dict(zip(_AMAP_PARAMS.get(method, ()),
                             sig.get("args") or [], strict=False))
            view.update(sig.get("kwargs") or {})
            tool = f"amap_{method}"
        else:
            view = sig.get("kwargs") or {}
            dest = (view.get("destination") or view.get("dest_name")
                    or view.get("city_name") or "")
            if not dest:
                continue
            tool = method
        query = f"{tool} {json.dumps(view, ensure_ascii=False)}"
        data = env.data
        if hasattr(data, "model_dump"):
            data = data.model_dump(mode="json")
        cid = repo.cache_search(pid, query, tool, data)
        snapshot_ids.append(cid)
        key = _live_key(method, view)
        if key:
            live_refs[key] = f"{label} 快照 {cid}（{datetime.now():%Y-%m-%d} 查询）"
    return len(recorder.calls)
