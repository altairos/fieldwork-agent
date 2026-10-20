"""async 工具回合循环（spec §3.3）：流式 LLM + 并行工具 + 事件发射。"""
import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any

from fieldwork_agent.kernel.guidance.rule_replies import rule_based_reply
from fieldwork_agent.kernel.guidance.safety import (
    INPUT_REFUSAL,
    SAFETY_BLOCK_NOTICE,
    check_model_output,
    check_user_input,
)
from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.registry import build_tool_schemas
from fieldwork_agent.kernel.research_state import turn_memory_patch
from fieldwork_agent.kernel.source_requests import SourceBudget
from fieldwork_agent.kernel.sources.flyai import RecordingClient, current_call_id
from fieldwork_agent.runtime.doc_args import merge_doc_args
from fieldwork_agent.runtime.literature import LiteratureSearchBudget
from fieldwork_agent.runtime.loop import (
    _assistant_message,
    _budget_skip_content,
    _coerce_args,
    _error_dict,
    _tool_content,
)
from fieldwork_agent.runtime.research_workflow import bind_research_searches
from fieldwork_agent.runtime.turn_buffer import TurnPatchBuffer
from fieldwork_agent.web_host.agent.context import build_messages
from fieldwork_agent.web_host.agent.dispatch import build_dispatch_fn
from fieldwork_agent.web_host.agent.state_tools import build_state_tools
from fieldwork_agent.web_host.turn_manager import TurnManager

# 无 LLM 降级提示：web 宿主无启动横幅渠道，提示附在回复末尾（CLI 宿主用启动横幅呈现）
_DEGRADED_NOTE = ("\n\n（提示：当前未配置 DashScope API Key，处于降级模式，"
                  "以上为规则引导文案；配置后可启用智能对话。）")

# 快捷选项标记：LLM 在枚举型提问末尾输出 [[options]]甲|乙|丙[[/options]]，
# 轮末剥离标记、选项入消息 meta 与 turn_done payload（渐进增强：无标记则无选项）
_OPTIONS_TAG_RE = re.compile(
    r"\[\[options(?::([^\]]*))?\]\](.*?)\[\[/options\]\]", re.S)
_MAX_OPTION_GROUPS = 3        # 兼容旧对话的解析容错；新策略默认一个主问，不据此剪裁正文
_MAX_OPTIONS_PER_GROUP = 4
_MAX_OPTION_LEN = 20
_MAX_LABEL_LEN = 12

# N05/R13 统一：当前工具调用实例 id（call_id）——kernel 侧定义的唯一 contextvar
# （kernel/sources/flyai.py current_call_id），_exec_tool 整段置位；RecordingClient
# 记录时读为 sig["call_id"]（快照 parent_call_id 配对键），宿主侧再经 tag_provider
# 读为 sig["tag"]（同规格多次调用按调用实例绑定快照）。asyncio.gather 为每个
# 协程建独立 Task（各自 context 副本），to_thread 再拷贝当前 context，故并发不串号。
_CURRENT_CALL_ID = current_call_id


def _extract_quick_replies(reply: str) -> tuple[str, list[dict]]:
    """剥离所有 [[options[:标签]]] 标记块；返回 (干净正文, groups)。

    每个块 → 一组 ``{"label": str, "options": list[str]}``：options 按 | 切分、
    strip、滤空，每组最多 4 项、每项截 20 字；label 为可选的 ``:标签``（截 12
    字，无标签则 ""）。无有效选项的块跳过（不产出空组）；组数上限 3。
    一个可枚举问题对应一组，多组时正文剥离全部块；无任何有效组 → (原文, [])
    （标记原样保留，与历史行为一致）。
    """
    matches = list(_OPTIONS_TAG_RE.finditer(reply))
    if not matches:
        return reply, []
    groups: list[dict] = []
    for m in matches:
        options = [opt.strip()[:_MAX_OPTION_LEN] for opt in m.group(2).split("|")]
        options = [opt for opt in options if opt][:_MAX_OPTIONS_PER_GROUP]
        if not options:
            continue
        label = (m.group(1) or "").strip()[:_MAX_LABEL_LEN]
        groups.append({"label": label, "options": options})
        if len(groups) >= _MAX_OPTION_GROUPS:
            break
    if not groups:
        return reply, []
    return _OPTIONS_TAG_RE.sub("", reply).rstrip(), groups


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


def _stop_requested(handle, start: float, max_wall_seconds: float) -> bool:
    """N13：取消已置位或超过 wall 截止 → 应停止。每次消费流帧后、启动工具前统一
    检查，尽早「停止副作用」（不再启动新工具/不再消费后续流帧）。已启动的线程
    调用不可撤回，故本检查只保证不再发起新动作、不承诺硬截止；handle 由调用方保证非 None。
    """
    return handle.cancel_event.is_set() or (time.monotonic() - start) > max_wall_seconds


async def run_turn(repo, pid: str, turn_id: str, user_text: str,
                   tm: TurnManager, llm: Any, flyai_client: Any, *,
                   amap_client: Any = None,
                   max_iters: int = 8, max_tool_calls: int = 20,
                   max_wall_seconds: float = 60.0) -> None:
    """执行完整回合：流式 LLM + 并行工具 + 事件发射 + 阶段推进 + 持久化。"""
    recorder = (RecordingClient(flyai_client, tag_provider=_CURRENT_CALL_ID.get)
                if flyai_client is not None else None)
    # N5：amap 同款双包装（spec §5.1 快照链对高德原语同样成立）——聚合层
    # 经 dispatch 拿到的是 recorder 透传视图，实际调用被逐次记录
    amap_recorder = (RecordingClient(amap_client, tag_provider=_CURRENT_CALL_ID.get)
                     if amap_client is not None else None)
    live_refs: dict[str, str] = {}
    # N05：按调用实例（call_id）分组的规格键→快照引用——避免整回合共享
    # 一个可覆盖的规格→cid 字典（同规格多次调用时后者不覆盖前者的绑定）。
    live_refs_by_call: dict[str, dict[str, str]] = {}
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
    deadline = time.monotonic() + max_wall_seconds
    def sources_stopped():
        handle = tm.get_turn(pid, turn_id)
        return handle is None or handle.cancel_event.is_set() or time.monotonic() >= deadline
    dispatch = SourceBudget(namespace=pid, cancelled=sources_stopped, deadline=deadline).wrap(dispatch)
    dispatch = LiteratureSearchBudget().wrap(dispatch)
    tools = build_tool_schemas()
    # F05：回合级 patch 暂存——状态工具与时窗回写先进 buffer，轮末正常结束
    # commit 落盘、取消 discard，项目状态不受残缺回合影响
    buffer = TurnPatchBuffer(repo, pid)
    dispatch = bind_research_searches(
        dispatch, repo, pid, context_provider=buffer.staged_view,
        on_snapshot=snapshot_ids.append)
    state_schemas, state_handlers = build_state_tools(repo, pid, buffer=buffer)
    tools = tools + state_schemas
    phase = repo.get_phase(pid)

    # R4：用户消息即时持久化（F09 修正：先 build 后 append——
    # build_messages 读 history 后自行 append 当前消息，顺序颠倒会双发）
    messages = build_messages(repo, pid, user_text, phase)
    # N12：用户消息绑定 turn_id（入 metadata）——retry 据此校验尾部归属，不误删他人轮
    repo.append_message(pid, "user", user_text, {"turn_id": turn_id})

    # 内容安全输入拦截（create/retry 汇于 run_turn，一处覆盖两路径）：
    # 用户消息保留原文落库（审计留痕），命中 → 固定拒答 + completed 终态；
    # 不消耗模型调用、不跑抽取/记忆/阶段推进（拒答轮不污染研究上下文）。
    verdict = check_user_input(user_text)
    if verdict.blocked:
        tm.append_event(pid, turn_id, "message_delta", {"text": INPUT_REFUSAL})
        tm.append_event(pid, turn_id, "safety_blocked",
                        {"side": "input", "category": verdict.category})
        repo.append_message(pid, "assistant", INPUT_REFUSAL,
                            {"turn_id": turn_id, "safety_blocked": True})
        tm.append_event(pid, turn_id, "turn_done",
                        {"reply_id": turn_id, "usage": {}, "snapshot_ids": [],
                         "quick_replies": [], "quick_reply_groups": [],
                         "finish_reason": "stop", "incomplete": False,
                         "safety_blocked": True})
        tm.finish(pid, turn_id, "completed")
        return


    iterations = 0
    total_calls = 0
    start = time.monotonic()
    accumulated_reply = ""
    stream_prefix = ""          # 前导语文本（已流出，重问后仍拼回持久化正文）
    preamble_retried = False    # 只重问一次，防无限循环
    incomplete = False          # 回合未收敛出正文 → turn_done.incomplete
    has_final_answer = False
    recovery_failed = False     # 最近一次续流失败（R18：成功清除，非粘滞标记）
    continuations = 0           # 已用续流预算（R2：四类续流共用）
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
                reply = rule_based_reply(phase, repo.get_research_context(pid)) + _DEGRADED_NOTE
                tm.append_event(pid, turn_id, "message_delta", {"text": reply})
                accumulated_reply = reply
                has_final_answer = True  # R16：规则文案即降级路径的最终答案
                break

            # 流式 LLM
            stream = llm.chat_stream(messages, tools=tools, slot="interview")
            iterations += 1
            final_resp = None
            async for chunk in _async_iter(stream):
                if tm.get_turn(pid, turn_id) is None:
                    return  # 项目删除竞态：停止消费已无消费者的流
                if _stop_requested(handle, start, max_wall_seconds):
                    break   # N13：消费中取消/超时 → 停止消费，不再处理后续帧（含终帧工具调用）
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
                        and continuations < MAX_CONTINUATIONS
                        and _looks_like_preamble(raw)):
                    # 前导语（"我这就去查…"/"先问最实际的："）不是最终回答：文本
                    # 已流出，记入前缀；带工具重问一次让它真正动手。length 不走此
                    # 分支（截断续写仍然 tools=None，保持既有语义）。
                    preamble_retried = True
                    stream_prefix += raw
                    continuations += 1  # R2：计入统一续流预算
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "system", "content": _PREAMBLE_NUDGE})
                    continue
                if answer and (finish_reason == "length" or not _looks_like_preamble(raw)):
                    has_final_answer = True
                break

            # N13：启动工具前检查取消与 deadline——即便终帧已带 tool_calls，此刻已
            # 取消/超时也不再启动新工具（不发 tool_started、不产生副作用），转轮末终态
            if _stop_requested(handle, start, max_wall_seconds):
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
                                 "data": data, "degraded": env.degraded, "source_usage": env.usage,
                                 "error": _error_dict(env)})
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": _tool_content(name, env)})
                if name == "analyze_fieldwork_window" and env.ok:
                    dest = str(call_args_map.get(call_id, {}).get("destination", ""))
                    cid = repo.cache_search(pid, f"time_window::{dest}",
                                            "analyze_fieldwork_window", data,
                                            parent_call_id=call_id, turn_id=turn_id,
                                            query_key=_query_key(
                                                "time_window", {"dest": dest}))
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
                                       live_refs, drained, turn_id=turn_id,
                                       live_refs_by_call=live_refs_by_call)
            # 双 recorder 各自游标（合并视图会错位）；snapshot_ids/live_refs
            # 共享：turn_done 汇总全部快照，轮末替换按键前缀天然不冲突
            drained_amap = _drain_snapshots(repo, pid, amap_recorder,
                                            snapshot_ids, live_refs,
                                            drained_amap, turn_id=turn_id,
                                            label="amap",
                                            live_refs_by_call=live_refs_by_call)

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
            and not _stop_requested(handle, start, max_wall_seconds)
            and continuations < MAX_CONTINUATIONS
            and _looks_truncated(accumulated_reply, finish_reason)):
        continuations += 1  # R2：计入统一续流预算
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
                if _stop_requested(handle, start, max_wall_seconds):
                    break   # N13：续写流消费中取消/超时 → 停止，已流出增量仍拼入
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
                recovery_failed = False  # R18：恢复成功即清除
            else:
                # 无终帧/终帧报错：已流出的续写增量照样拼入，绝不丢
                accumulated_reply += cont_deltas
                recovery_failed = True  # R18：最近一次恢复失败
        except Exception:
            # 流中断：同上兜底（终审 B3），不丢已流出的内容
            accumulated_reply += cont_deltas
            recovery_failed = True  # R18

    # 最终回答收敛（对齐 CLI runtime/loop.py 的触顶收敛；F06 收敛条件整体
    # 替换为 not has_final_answer）：主循环退出但没拿到最终回答（预算触顶
    # 裸退出、思考模型「只想不写」、或只输出了前导语就触顶）时不让回合静默
    # 结束——追加 system 收敛指令再取一次最终回答（不带工具，防继续调用）；
    # 仍空则按空/非空走三分支兜底。cancel 已取消的回合不做兜底（终态 cancelled）。
    handle = tm.get_turn(pid, turn_id)
    if handle is None:
        return  # 项目删除竞态：收敛与轮末的写入目标均已消失
    if (llm is not None and not has_final_answer
            and not _stop_requested(handle, start, max_wall_seconds)
            and continuations < MAX_CONTINUATIONS):
        continuations += 1  # R2：计入统一续流预算
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
                if _stop_requested(handle, start, max_wall_seconds):
                    break   # N13：收敛流消费中取消/超时 → 停止，走轮末终态
                if "delta" in chunk and chunk["delta"]:
                    tm.append_event(pid, turn_id, "message_delta",
                                    {"text": chunk["delta"]})
                elif "content" in chunk:
                    final_resp = chunk
            if final_resp and not final_resp.get("error"):
                # R16：接管收敛帧自身的 finish_reason——收敛产出以 length/
                # 未完标点收尾时，旧代码沿用主循环的 stop，终态伪装完整。
                finish_reason = (str(final_resp.get("finish_reason") or "")
                                 or finish_reason)
                recovery_failed = False  # R18：恢复成功即清除
                if (final_resp.get("content") or "").strip():
                    accumulated_reply = stream_prefix + final_resp["content"]
                    has_final_answer = True  # 收敛成功即最终回答，三分支不再追加提示
                usage_total = final_resp.get("usage", {}) or usage_total
            else:
                recovery_failed = True  # R18：收敛帧缺失/报错（此前无此分支，静默）
        except Exception:
            accumulated_reply = ""  # 收敛重试失败不改变回合结果，走兑底文案
            recovery_failed = True  # R18：收敛流中断
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
    # 悬挂补完（R6）：以冒号/空选项块收尾的半截话不是可交付答案——
    # 生产实录回合「先问最实际的：」后无下文。追加一次无工具续流让模型
    # 把话补完；预算/墙钟/取消三重闸门；失败保留原文（已流出内容绝不丢）。
    if (not cancelled and llm is not None and accumulated_reply
            and continuations < MAX_CONTINUATIONS
            and not _stop_requested(handle, start, max_wall_seconds)
            and _looks_dangling(accumulated_reply)):
        continuations += 1
        messages.append({"role": "system", "content":
                         "你的上一条回复以冒号或空选项块收尾，话还没说完。"
                         "已输出内容的末尾如下：\n" + accumulated_reply[-120:]
                         + "\n请紧接着上述末尾把话说完：直接续写正文；若在等待"
                           "对方选择，用 [[options:标签]]选项A|选项B[[/options]] "
                           "格式给出选项；不要重复已写出的内容，写完即止。"})
        comp_deltas = ""  # 补完流已流出增量：与截断续写同款三路兜底
        try:
            stream = llm.chat_stream(messages, tools=None, slot="interview")
            iterations += 1
            comp_resp = None
            async for chunk in _async_iter(stream):
                if tm.get_turn(pid, turn_id) is None:
                    return  # 项目删除竞态：同截断续写
                if "delta" in chunk and chunk["delta"]:
                    comp_deltas += chunk["delta"]
                    tm.append_event(pid, turn_id, "message_delta",
                                    {"text": chunk["delta"]})
                elif "content" in chunk:
                    comp_resp = chunk
            if comp_resp and not comp_resp.get("error"):
                accumulated_reply += ((comp_resp.get("content", "") or "")
                                      or comp_deltas)
                finish_reason = (str(comp_resp.get("finish_reason") or "")
                                 or finish_reason)
                usage_total = comp_resp.get("usage", {}) or usage_total
                recovery_failed = False  # R18：补完成功即清除
            else:
                accumulated_reply += comp_deltas
                recovery_failed = True  # R18：无终帧/终帧报错
        except Exception:
            accumulated_reply += comp_deltas  # 流中断：已流出内容绝不丢
            recovery_failed = True  # R18

    # 悬挂补完块的 await 窗口内 cancel 可能已置位（TurnManager.cancel 会
    # 立即 finish("cancelled")）——刷新快照，防已取消回合被改写 completed
    cancelled = handle.cancel_event.is_set() or cancelled
    if not cancelled:
        # 统一终检门（R8/R16/R18）：所有恢复路径走完后的最后一道防线——
        # 无答案 / 最近一次恢复失败 / 仍截断 / 仍悬挂，任一命中即 incomplete。
        # 检查的是提取 quick_replies 之前的原始 accumulated_reply（有效选项
        # 块收尾不算悬挂，见 _looks_dangling）。
        if ((not has_final_answer) or recovery_failed
                or _looks_truncated(accumulated_reply, finish_reason)
                or _looks_dangling(accumulated_reply)):
            incomplete = True
    quick_reply_groups: list[dict] = []
    safety_blocked = False
    if not cancelled and accumulated_reply:
        accumulated_reply, quick_reply_groups = _extract_quick_replies(accumulated_reply)
        accumulated_reply = _normalize_cjk_quotes(accumulated_reply)
        # 输出终检（窄表）：命中 → 落库/turn_done 载体替换为固定文案；
        # 已流出 delta 不撤回（流式诚实），拦截目标是持久层与后续上下文。
        out_verdict = check_model_output(accumulated_reply)
        if out_verdict.blocked:
            safety_blocked = True
            quick_reply_groups = []  # 替换文案不含选项块
            tm.append_event(pid, turn_id, "safety_blocked",
                            {"side": "output", "category": out_verdict.category})
            accumulated_reply = SAFETY_BLOCK_NOTICE
    # 旧扁平 quick_replies 仅单组时携带；多组时为空，避免只覆盖其中一个问题的误导性子集
    quick_replies: list[str] = (quick_reply_groups[0]["options"]
                                if len(quick_reply_groups) == 1 else [])
    if not cancelled:
        try:
            if accumulated_reply:
                # N12：turn_id 入 meta（retry 校验尾部归属）；quick_reply_groups 为
                # 分支特性，在下方条件区自动共存
                meta = {"turn_id": turn_id,
                        "tool_calls": total_calls, "finish_reason": finish_reason}
                if quick_replies:
                    meta["quick_replies"] = quick_replies
                if quick_reply_groups:
                    meta["quick_reply_groups"] = quick_reply_groups
                if safety_blocked:
                    meta["safety_blocked"] = True
                repo.append_message(pid, "assistant", accumulated_reply, meta)
            if accumulated_reply:
                buffer.stage(turn_memory_patch(buffer.staged_view(), user_text, accumulated_reply))
            buffer.commit()  # F05：正常轮末提交暂存 patch（取消/失败路径不落盘）
            _persist_tool_results(repo, pid, tm, turn_id, call_name_map, live_refs,
                                  doc_ctx_provenance, live_refs_by_call)
            _apply_extractions(repo, pid, user_text)
            _schedule_autoname(repo, pid, llm)   # N09：后台任务卸载，不阻塞事件循环/主回合
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
                         "quick_reply_groups": quick_reply_groups,
                         "finish_reason": finish_reason,
                         "incomplete": incomplete,
                         "safety_blocked": safety_blocked})
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
                   "请补齐能够独立成立的回应：需要澄清时围绕当前研究目标提一个主问题，"
                   "信息足够时直接整理或交付并自然结束；只有需要外部真实数据时才调用工具。"
                   "不要为补全前导语强制调用工具或追加问题，不要再停在「我这就去查」。")


# 续流统一预算（R2）：前导语重问 / 截断续写 / 收敛兜底 / 悬挂补完共用
# 一个计数——任一回合最坏情形封顶 MAX_CONTINUATIONS 次额外 LLM 调用。
MAX_CONTINUATIONS = 3


def _stop_requested(handle, start: float, max_wall_seconds: float) -> bool:
    """续流前统一闸门：取消或墙钟触顶（命名对齐 main 分支 N13，便于合并）。"""
    return handle.cancel_event.is_set() or time.monotonic() - start > max_wall_seconds


def _looks_like_preamble(text: str) -> bool:
    """前导语判定：以未完标点收尾，或短句且含承诺词、但未以句末标点收尾。"""
    # 可回应的选项已构成决策提示（兼容旧“先定两项：+选项”），不强制修复成工具调用。
    matches = list(_OPTIONS_TAG_RE.finditer(text))
    if any(len([opt for opt in match.group(2).split("|") if opt.strip()]) >= 2
           for match in matches):
        return False
    # 快捷选项是展示标记；完整问句后附空/坏选项，也不应变成前导语。
    body = _OPTIONS_TAG_RE.sub("", text).strip()
    if not body:
        return False
    if body[-1] in _PREAMBLE_TAIL_CHARS:
        return True
    return (len(body) <= 120 and body[-1] not in "。！？.!?"
            and any(marker in body for marker in _PROMISE_MARKERS))


def _looks_truncated(reply: str, finish_reason: str) -> bool:
    """终止帧是否截断：length 触顶，或正文尾部落在未完标点/截断算式上。"""
    if finish_reason == "length":
        return True
    tail = reply.rstrip()
    return bool(tail) and tail[-1] in _INCOMPLETE_TAIL_CHARS


# 悬挂尾字符（R6）：以全/半角冒号收尾 = 话没说完。冒号不在
# _INCOMPLETE_TAIL_CHARS（前导语走带工具重问），此处是轮末最后一道判停。
_DANGLING_TAIL = "：:"


def _looks_dangling(reply: str) -> bool:
    """悬挂判定（R6）：只看收尾结构，不做全文豁免。

    ① rstrip 后以冒号收尾；② 以 options 块收尾（块后无正文）且块内无有效
    选项——复用 _OPTIONS_TAG_RE 与解析器同源（按 | 切分、strip、滤空）。
    完整句、有效选项块均不算悬挂；前文出现过完整 options 块不影响判定
    （只看最后一个块之后的收尾）。
    """
    body = reply.rstrip()
    if not body:
        return False
    if body[-1] in _DANGLING_TAIL:
        return True
    matches = list(_OPTIONS_TAG_RE.finditer(body))
    if matches:
        last = matches[-1]
        if not body[last.end():].strip():
            inner = (last.group(2) or "").strip()
            return not [opt for opt in inner.split("|") if opt.strip()]
    return False


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
    # R13/N05：整段置位 call_id 上下文（唯一 contextvar，见模块头注释）——
    # asyncio.to_thread 传播 contextvars（聚合内部线程池经 copy_context().run
    # 继续），RecordingClient 记录时取值：sig["call_id"] 供快照 parent_call_id
    # 与工具调用配对（R13），sig["tag"] 供轮末按调用实例绑定快照（N05）
    token = current_call_id.set(call_id)
    try:
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
    finally:
        current_call_id.reset(token)



def _query_key(tool: str, view: dict) -> str:
    """同题分组键（R14）：工具名 + 归一化参数 canonical JSON（键排序；不可序列化值 str 降级）。

    与 RecordingClient 的 view（bind+apply_defaults，R17）同源——直通与聚合等效查询同键。
    """
    canonical = json.dumps(view, ensure_ascii=False, sort_keys=True, default=str)
    return f"{tool}|{canonical}"


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
        radius = kwargs.get("radius", 5000)
        suffix = f"|radius={radius}" if radius != 5000 else ""
        return (f"@live:amap_around_search:{keywords}|{location}{suffix}" if keywords and location else "")
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
                          doc_ctx_provenance=None, live_refs_by_call=None):
    """轮末把成功工具结果落盘；数据先经 _resolve_live_refs 替换规格键（R08）。

    N04：``doc_ctx_provenance``（call_id → 生成时仓储依赖视图）供 compose_proposal
    落盘时作为 provenance_ctx 传给 save_proposal，使依赖指纹忠于生成时的
    仓储依赖版本而非保存时推测（并发外部修改时能正确判失效）。
    N05：``live_refs_by_call``（call_id → 规格键→引用）使每份产物按自己的
    调用实例解析快照（本调用优先，全局 live_refs 作跨调用回退）。
    """
    doc_ctx_provenance = doc_ctx_provenance or {}
    live_refs_by_call = live_refs_by_call or {}
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
        # N05：按调用实例解析——本调用记录的规格键优先（同规格不互相覆盖），
        # 全局 live_refs 作跨调用回退（保障无 tag 历史产物仍能解析）
        effective_refs = {**live_refs, **live_refs_by_call.get(call_id, {})}
        data_dict = _resolve_live_refs(data_dict, effective_refs)
        if name == "profile_field_site":
            repo.save_field_site(pid, {"name": data_dict.get("location", ""),
                                       "confirmed": False, "profile": data_dict})
        elif name == "compose_itinerary":
            dests = ",".join(data_dict.get("destinations", []) or [])
            parent = next((cid for cid, n in call_name_map.items()
                           if n == "compose_itinerary"), "")
            repo.cache_search(pid, f"itinerary::{dests}", "compose_itinerary", data_dict,
                              parent_call_id=parent, turn_id=turn_id,
                              query_key=_query_key("itinerary", {"dests": dests}))
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
    （用户手动命名后永不覆盖）。写入走 update_research_context 浅合并，仅覆盖 title 键。

    N09：本函数含阻塞的 ``llm.chat``，生产路径由 ``_schedule_autoname`` 经
    ``asyncio.to_thread`` 卸载到线程、以 fire-and-forget 后台任务执行（不卡事件
    循环、主回合先完成）。为防命名窗口内的用户改名/编辑被覆盖，耗时的 llm.chat
    前后各核对一次 title 与项目版本（updated_at）：返回时 title 已非空或版本已变
    则放弃写入。同步直调（测试）无并发，复核天然通过。
    """
    try:
        ctx = repo.get_research_context(pid)
        if str(ctx.get("title") or "").strip():
            return
        version_before = repo.get_state(pid).get("updated_at")   # N09：命名前项目版本基线
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
        # N09：返回时复核——title 仍空（用户未在窗口内命名）且项目版本未变（无并发
        # 编辑）才写入，绝不覆盖命名窗口期间的用户改名/编辑。
        if str(repo.get_research_context(pid).get("title") or "").strip():
            return
        if repo.get_state(pid).get("updated_at") != version_before:
            return
        repo.update_research_context(pid, {"title": name})
    except Exception:
        return   # best-effort：失败不影响回合流程，下回合再试


# N09：自动命名后台任务引用集——fire-and-forget 须持引用，防 GC 提前回收任务
_AUTONAME_TASKS: set[asyncio.Task] = set()
# 命名模型调用的短超时（秒）：超时即放弃，best-effort 不拖累 worker
_AUTONAME_TIMEOUT = 8.0


async def _autoname_async(repo, pid, llm):
    """N09：把阻塞的 ``_maybe_autoname`` 卸载到线程并加短超时——事件循环不被命名
    模型调用阻塞（心跳/其他请求/SSE/取消照常及时处理）。best-effort：超时或任何
    异常静默放弃、下回合再试；CancelledError 属 BaseException，会正常传播（任务被
    取消时干净终止，不吞取消）。
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_maybe_autoname, repo, pid, llm),
            timeout=_AUTONAME_TIMEOUT)
    except Exception:
        return


def _schedule_autoname(repo, pid, llm):
    """N09：轮末以 fire-and-forget 后台任务触发自动命名——主回合不等待、先完成。

    ``create_task`` 不产生 await 点，轮末块「同步无 await、取消不中途插入」的不变量
    得以保持；任务在 run_turn 返回、事件循环空闲后才跑，其版本基线天然晚于本轮全部
    写入（append_message/抽取/阶段推进），故 updated_at 复核只反映外部并发编辑。
    无运行中事件循环（同步直调/脚本）时静默跳过，下回合再试。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_autoname_async(repo, pid, llm))
    _AUTONAME_TASKS.add(task)
    task.add_done_callback(_AUTONAME_TASKS.discard)


# amap client 方法形参序（N5）：RecordingClient 记录的位置参数按此并入
# 参数视图——geocode 在 last_mile 为位置传参、site_profile 为关键字传参，
# 两种形态都归一到同名视图，供快照 query 与 _live_key 取参
_AMAP_PARAMS = {
    "geocode": ("address", "city"),
    "regeocode": ("location",),
    "text_search": ("keywords", "city", "page_size", "city_limit"),
    "around_search": ("keywords", "location", "radius", "page_size"),
    "direction_transit": ("origin", "destination", "city", "cityd", "date"),
    "weather": ("city", "extensions"),
}


def _drain_snapshots(repo, pid, recorder, snapshot_ids, live_refs, start: int,
                      turn_id: str, label: str = "flyai",
                      live_refs_by_call=None) -> int:
    """消费 recorder.calls[start:] 的新增成功搜索，落盘为快照证据（R08 §6）。

    返回新游标（幂等，同一调用不会重复落盘）。参数视图优先取 ``sig["view"]``
    （RecordingClient 归一化视图，R17），缺失时回落旧拼装（amap 位置参数按
    ``_AMAP_PARAMS`` 并入）。flyai 路径仅记录带目的地语义的调用
    （destination/dest_name/city_name 任一命中）；amap 路径（label="amap"）
    六原语全为地理查询，全部落盘且快照 tool 名带 ``amap_`` 前缀区分来源。
    query 为可读的 ``工具名 {完整参数视图 JSON}``；analyze_fieldwork_window
    内部的多日期采样同样经 recorder 被捕获——它们正是价格曲线的原始依据。
    落盘携带 ``parent_call_id``（产生该查询的工具 call_id，R13）、
    ``turn_id``（当前回合 id，必选参）与 ``query_key``（同题分组键，R14）。
    每次落盘同时按 ``_live_key`` 归并 规格键→快照引用（后写覆盖：同一键
    多次命中取最新），轮末 ``_persist_tool_results`` 据此替换聚合产出中
    的规格键；文案前缀随 label（flyai/amap 快照）。
    N05：``live_refs_by_call`` 非 None 时，另按调用实例（sig["tag"] 即 call_id）
    分组存一份规格键→引用，使同规格多次调用不会互相覆盖（“同规格≠同一次报价”）。
    recorder 为 None（宿主未注入对应客户端）时原样返回游标。
    """
    if recorder is None:
        return start
    for method, sig, env in recorder.calls[start:]:
        if not getattr(env, "ok", False):
            continue
        view = sig.get("view")
        if view is None:
            # 防御：view 缺失（理论不达——RecordingClient 同版本必写）回落旧拼装
            if label == "amap":
                view = dict(zip(_AMAP_PARAMS.get(method, ()),
                                sig.get("args") or [], strict=False))
                view.update(sig.get("kwargs") or {})
            else:
                view = dict(sig.get("kwargs") or {})
        # R17：**kw-only 委托客户端（测试替身/动态代理）兜底——bind 对 **kw 形态产生
        # {"kw": {...}} 单键包裹，解包还原；真客户端 8 原语全具体形参不受影响。
        if set(view) == {"kw"} and isinstance(view["kw"], dict):
            view = view["kw"]
        if label == "amap":
            tool = f"amap_{method}"
        else:
            dest = (view.get("destination") or view.get("dest_name")
                    or view.get("city_name") or "")
            if not dest:
                continue
            tool = method
        query = f"{tool} {json.dumps(view, ensure_ascii=False, default=str)}"
        data = env.data
        if hasattr(data, "model_dump"):
            data = data.model_dump(mode="json")
        cid = repo.cache_search(pid, query, tool, data,
                                parent_call_id=str(sig.get("call_id") or ""),
                                turn_id=turn_id,
                                query_key=_query_key(tool, view))
        snapshot_ids.append(cid)
        key = _live_key(method, view)
        if key:
            ref = f"{label} 快照 {cid}（{datetime.now():%Y-%m-%d} 查询）"
            live_refs[key] = ref   # 全局（后写覆盖，作跨调用回退）
            if live_refs_by_call is not None:
                # N05：按调用实例分组——同规格不同次调用各自绑定自己的快照
                tag = sig.get("tag", "") or ""
                live_refs_by_call.setdefault(tag, {})[key] = ref
    return len(recorder.calls)
