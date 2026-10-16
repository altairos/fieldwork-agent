"""async 工具回合循环（spec §3.3）：流式 LLM + 并行工具 + 事件发射。"""
import asyncio
import time
from typing import Any

from fieldwork_agent.kernel.guidance.rule_replies import rule_based_reply
from fieldwork_agent.kernel.registry import build_tool_schemas
from fieldwork_agent.runtime.loop import _assistant_message, _coerce_args, _tool_content
from fieldwork_agent.web_host.agent.context import build_messages
from fieldwork_agent.web_host.agent.dispatch import build_dispatch_fn
from fieldwork_agent.web_host.turn_manager import TurnManager

_SEARCH = frozenset({"search_flights", "search_trains", "search_hotels",
                     "search_marriott_hotels", "search_marriott_packages",
                     "search_pois", "search_keyword", "search_ai"})

# 无 LLM 降级提示：web 宿主无启动横幅渠道，提示附在回复末尾（CLI 宿主用启动横幅呈现）
_DEGRADED_NOTE = ("\n\n（提示：当前未配置 DashScope API Key，处于降级模式，"
                  "以上为固定引导文案；配置后可启用智能对话。）")


async def run_turn(repo, pid: str, turn_id: str, user_text: str,
                   tm: TurnManager, llm: Any, flyai_client: Any, *,
                   max_iters: int = 8, max_tool_calls: int = 20,
                   max_wall_seconds: float = 60.0) -> None:
    """执行完整回合：流式 LLM + 并行工具 + 事件发射 + 阶段推进 + 持久化。"""
    dispatch = build_dispatch_fn(flyai_client, llm)
    tools = build_tool_schemas()
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
                tm.append_event(pid, turn_id, "tool_started",
                                {"call_id": call_id, "tool": name,
                                 "args": args, "purpose": ""})
                coros.append(_exec_tool(dispatch, name, args, call_id))

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
                        {"reply_id": turn_id, "usage": usage_total})
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


async def _exec_tool(dispatch, name, args, call_id):
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
        if name in _SEARCH:
            repo.cache_search(pid, f"{name}: {call_id}", name, data_dict)
        elif name == "profile_field_site":
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
    result = phases.check_advance(
        state["phase"], ctx, sites, informants,
        has_itinerary, bool(state.get("proposals")),
        bool(state.get("proposal_confirmed")))
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
