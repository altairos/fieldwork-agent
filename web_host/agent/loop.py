"""async 工具回合循环（spec §3.3）：流式 LLM + 并行工具 + 事件发射。"""
import asyncio
import json
import time
from datetime import datetime
from typing import Any

from fieldwork_agent.kernel.guidance.rule_replies import rule_based_reply
from fieldwork_agent.kernel.registry import build_tool_schemas
from fieldwork_agent.kernel.sources.flyai import RecordingClient
from fieldwork_agent.runtime.loop import _assistant_message, _coerce_args, _tool_content
from fieldwork_agent.web_host.agent.context import build_messages
from fieldwork_agent.web_host.agent.dispatch import build_dispatch_fn
from fieldwork_agent.web_host.agent.state_tools import build_state_tools
from fieldwork_agent.web_host.turn_manager import TurnManager

# 无 LLM 降级提示：web 宿主无启动横幅渠道，提示附在回复末尾（CLI 宿主用启动横幅呈现）
_DEGRADED_NOTE = ("\n\n（提示：当前未配置 DashScope API Key，处于降级模式，"
                  "以上为固定引导文案；配置后可启用智能对话。）")


async def run_turn(repo, pid: str, turn_id: str, user_text: str,
                   tm: TurnManager, llm: Any, flyai_client: Any, *,
                   max_iters: int = 8, max_tool_calls: int = 20,
                   max_wall_seconds: float = 60.0) -> None:
    """执行完整回合：流式 LLM + 并行工具 + 事件发射 + 阶段推进 + 持久化。"""
    recorder = RecordingClient(flyai_client) if flyai_client is not None else None
    dest_refs: dict[str, str] = {}
    snapshot_ids: list[str] = []
    drained = 0
    call_args_map: dict[str, dict] = {}
    dispatch = build_dispatch_fn(recorder if recorder is not None else flyai_client,
                                 llm, live_snapshot_refs=dest_refs)
    tools = build_tool_schemas()
    state_schemas, state_handlers = build_state_tools(repo, pid)
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
    usage_total: dict = {}
    call_name_map: dict[str, str] = {}

    try:
        while True:
            handle = tm.get_turn(pid, turn_id)
            if handle and handle.cancel_event.is_set():
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
            accumulated_reply = final_resp.get("content", "") or accumulated_reply
            usage_total = final_resp.get("usage", {}) or usage_total
            tool_calls = final_resp.get("tool_calls")
            if not tool_calls:
                break

            # 并行工具执行
            messages.append(_assistant_message(final_resp))
            coros = []
            for call in tool_calls:
                name = call.get("name", "")
                call_id = call.get("id", "")
                args = _coerce_args(call.get("arguments"))
                call_name_map[call_id] = name
                call_args_map[call_id] = args
                tm.append_event(pid, turn_id, "tool_started",
                                {"call_id": call_id, "tool": name,
                                 "args": args, "purpose": ""})
                coros.append(_exec_tool(dispatch, name, args, call_id, state_handlers))

            results = await asyncio.gather(*coros)
            total_calls += len(results)

            for call_id, name, env in results:
                data = env.data
                if hasattr(data, "model_dump"):
                    data = data.model_dump(mode="json")
                tm.append_event(pid, turn_id, "tool_completed",
                                {"call_id": call_id, "ok": env.ok,
                                 "data": data, "degraded": env.degraded})
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": _tool_content(name, env)})
                if name == "analyze_fieldwork_window" and env.ok:
                    dest = str(call_args_map.get(call_id, {}).get("destination", ""))
                    cid = repo.cache_search(pid, f"time_window::{dest}",
                                            "analyze_fieldwork_window", data)
                    snapshot_ids.append(cid)
            # 快照链（spec §5.1）：搜索即时落盘，下一轮工具调用（如 compose_itinerary）
            # 经 dispatch 注入的 dest_refs 即可引用刚落盘的快照
            drained = _drain_snapshots(repo, pid, recorder, snapshot_ids,
                                       dest_refs, drained)

    except Exception as exc:
        tm.append_event(pid, turn_id, "error",
                        {"code": "internal_error", "message": str(exc), "hint": ""})
        tm.finish(pid, turn_id, "failed")
        return

    # 轮末：助手消息持久化 + 工具结果落盘 + 抽取写回 + 阶段推进
    if accumulated_reply:
        repo.append_message(pid, "assistant", accumulated_reply,
                            {"tool_calls": total_calls})
    _persist_tool_results(repo, pid, tm, turn_id, call_name_map)
    _apply_extractions(repo, pid, user_text)
    transition = check_and_advance(repo, pid)
    if transition:
        tm.append_event(pid, turn_id, "phase_transition", transition)

    # 终态
    handle = tm.get_turn(pid, turn_id)
    if handle and handle.cancel_event.is_set():
        tm.finish(pid, turn_id, "cancelled")
    else:
        tm.append_event(pid, turn_id, "turn_done",
                        {"reply_id": turn_id, "usage": usage_total,
                         "snapshot_ids": snapshot_ids})
        tm.finish(pid, turn_id, "completed")


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
        env = local_handlers[name](**args)
    else:
        env = await asyncio.to_thread(dispatch, name, args)
    return (call_id, name, env)


def _persist_tool_results(repo, pid, tm, turn_id, call_name_map):
    """轮末把成功工具结果落盘。"""
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
        if name == "profile_field_site":
            repo.save_field_site(pid, {"name": data_dict.get("location", ""),
                                       "confirmed": False, "profile": data_dict})
        elif name == "compose_itinerary":
            dests = ",".join(data_dict.get("destinations", []) or [])
            repo.cache_search(pid, f"itinerary::{dests}", "compose_itinerary", data_dict)
        elif name == "compose_proposal":
            markdown = data_dict.get("markdown", "")
            if markdown:
                repo.save_proposal(pid, markdown,
                                   kind=data_dict.get("kind") or "proposal",
                                   version=data_dict.get("version") or "draft")
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


def _drain_snapshots(repo, pid, recorder, snapshot_ids, dest_refs, start: int) -> int:
    """消费 recorder.calls[start:] 的新增成功搜索，落盘为快照证据（spec §5.1）。

    返回新游标（幂等，同一调用不会重复落盘）。仅记录带目的地语义的调用
    （destination/dest_name/city_name 任一命中）；query 为可读的
    ``方法名 {参数 JSON}``；analyze_fieldwork_window 内部的多日期采样同样
    经 recorder 被捕获——它们正是价格曲线的原始依据。同一目的地多次
    命中取最新引用。recorder 为 None（宿主未注入 flyai）时原样返回游标。
    """
    if recorder is None:
        return start
    for method, sig, env in recorder.calls[start:]:
        if not getattr(env, "ok", False):
            continue
        kwargs = sig.get("kwargs") or {}
        dest = (kwargs.get("destination") or kwargs.get("dest_name")
                or kwargs.get("city_name") or "")
        if not dest:
            continue
        query = f"{method} {json.dumps(kwargs, ensure_ascii=False)}"
        data = env.data
        if hasattr(data, "model_dump"):
            data = data.model_dump(mode="json")
        cid = repo.cache_search(pid, query, method, data)
        snapshot_ids.append(cid)
        dest_refs[dest] = f"flyai 快照 {cid}（{datetime.now():%Y-%m-%d} 查询）"
    return len(recorder.calls)
