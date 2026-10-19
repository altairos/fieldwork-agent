"""runtime/loop.py — CLI/Web 共用的工具回合循环运行时库（R5：不属 kernel）。

kernel 只做无状态能力、禁止 LLM 编排；"发请求→执行 tool_calls→回填→再请求"
的共享循环归运行时层。本模块不绑定任何具体 LLM/能力，全部经参数注入：

- ``llm.chat(messages, tools=..., slot=...)`` 返回恒含 ``content``/``tool_calls``/
  ``finish_reason``（见 ``kernel.llm`` 契约）；
- ``dispatch(name, args) -> Envelope``：能力分派（宿主负责把任何异常折进包络）；
- 工具结果以**结构化摘要**回填 LLM 消息（R5：不整段倾倒 JSON），序列化后再设
  字符安全上限 ``TOOL_CONTENT_LIMIT``，杜绝单条工具结果撑爆上下文。

回合预算三重上限（R5）：迭代数 ``max_iters``（默认 8，spec §3.3）、累计工具调用数
``max_tool_calls``、墙钟总时长 ``max_wall_seconds``。任一命中即 ``stopped_by_limit=True``，
追加一条 system 提示后再取**一次**最终回答返回（优雅收敛，绝不裸断在半截工具链上）。
F07：一批 tool_calls 越过额度时**批前截断**——只执行剩余额度内的调用，被跳过者
立即回填 ``role=tool`` 的 budget_exceeded 消息（OpenAI 协议：每个 tool_call_id
必须有对应回填），不再整批全执行后才检查。
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fieldwork_agent.kernel.guidance.interview import INTERVIEW_DEGRADED_GUIDANCE
from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.runtime.literature import LiteratureSearchBudget

# 摘要序列化后进消息的字符安全上限（R5：结构化摘要仍须有界）
TOOL_CONTENT_LIMIT = 2000
# 摘要里最多列出的条目名数量
_TOP_ITEMS = 5
# 长字符串在摘要中的预览阈值
_PREVIEW = 160
_LITERATURE_CONTENT_LIMIT = 12000


def _literature_summary(env: Envelope) -> dict:
    """保留引用与筛选证据，不把文献分页压成只有标题的商品摘要。"""
    out: dict[str, Any] = {"tool": "search_literature", "ok": env.ok}
    if not env.ok:
        out["error"] = _error_dict(env)
    if env.degraded:
        out["degraded"] = env.degraded
    data = env.data.model_dump(mode="json") if hasattr(env.data, "model_dump") else env.data
    if not isinstance(data, dict):
        return out
    items = data.get("items") or []
    for key in ("returned_count", "retrieved_count", "unique_count", "duplicates_removed",
                "matched_total", "truncated", "warnings", "query_reports", "chinese_sources",
                "scope_fingerprint", "scope_basis"):
        if key in data:
            out[key] = data[key]
    # 即使处理旧缓存，实际返回数也从items计算，不信任旧total=meta.count。
    out["returned_count"] = len(items)
    out["items"] = [{
        "title": str(item.get("title") or "")[:240],
        "authors": [str(a)[:100] for a in (item.get("authors") or [])[:3]],
        "year": item.get("year"), "doi": item.get("doi", ""), "id": item.get("id", ""),
        "venue": str(item.get("venue") or "")[:120],
        "abstract": str(item.get("abstract") or "")[:500],
        "screening_basis": "abstract_excerpt" if item.get("abstract") else "title_only_unverified",
        "matched_queries": item.get("matched_queries", []),
    } for item in items[:8] if isinstance(item, dict)]
    out["summarized_count"] = len(out["items"])
    return out


@dataclass
class LoopResult:
    """``run_tool_loop`` 的返回：最终回复 + 工具事件 + 迭代数 + 是否触顶。"""

    reply: str
    tool_events: list[dict] = field(default_factory=list)
    iterations: int = 0
    stopped_by_limit: bool = False


def _brief(value: Any) -> Any:
    """把任意值压成摘要友好形态：长字符串→{len,preview}；容器→递归浅压缩。"""
    if isinstance(value, str):
        if len(value) <= _PREVIEW:
            return value
        return {"len": len(value), "preview": value[:_PREVIEW]}
    if isinstance(value, dict):
        return {k: _brief(v) for k, v in list(value.items())[:8]}
    if isinstance(value, list):
        return {"count": len(value), "sample": [_brief(v) for v in value[:2]]}
    return value


def _item_brief(item: dict) -> dict:
    """条目 → {name + price/rating 关键标量（有值才带）：给 LLM 真数据可引，
    防零工具轮把极值线索膨胀成编造的“实测数据”（店名/评分幻觉）。"""
    from fieldwork_agent.runtime.travel_summary import brief_item
    return brief_item(item)


def _error_dict(env: Envelope) -> dict | None:
    """失败包络 → {code,message,hint} 三元组；成功返回 None（事件/摘要共用形状）。"""
    if env.ok:
        return None
    err = env.error
    return {"code": getattr(err, "code", ""),
            "message": getattr(err, "message", ""),
            "hint": getattr(err, "hint", "")}


def _summarize(name: str, env: Envelope) -> dict:
    """Envelope → 结构化摘要（供 LLM 读；形状见模块 docstring 与 task 报告）。"""
    if name == "search_literature":
        return _literature_summary(env)
    out: dict[str, Any] = {"tool": name, "ok": env.ok}
    if env.usage:
        out["source_usage"] = env.usage
    if env.degraded:
        out["degraded"] = env.degraded
    if not env.ok:
        out["error"] = _error_dict(env)
        return out

    data = env.data
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")

    from fieldwork_agent.runtime.travel_summary import TRAVEL_TOOLS, compact
    if name in TRAVEL_TOOLS and not (isinstance(data, dict) and isinstance(data.get("items"), list)):
        out["fields"] = compact(data)
        return out

    # 搜索分页（ItemPage）：给极值与 top_items（name+price/rating），丢弃字段级噪声
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
        out["total"] = data.get("total", len(items))
        out["returned_count"] = len(items)
        out["raw_count"] = data.get("raw_count")
        out["entity_count"] = data.get("entity_count")
        if data.get("cheapest") is not None:
            out["cheapest"] = data["cheapest"]
        if data.get("dearest") is not None:
            out["dearest"] = data["dearest"]
        out["top_items"] = [_item_brief(it) for it in items[:_TOP_ITEMS]
                            if isinstance(it, dict)]
        # R1：ai-search 直答文本进摘要（answer-only 页 total=0，旧路径全丢）；
        # 长度不另设限——_tool_content 统一截到 TOOL_CONTENT_LIMIT
        if isinstance(data.get("answer"), str) and data["answer"].strip():
            out["answer"] = data["answer"]
        return out
    # 候选列表（suggest_informants 等）：给总数与前几条（name+关键标量）
    if isinstance(data, list):
        out["total"] = len(data)
        out["top_items"] = [b for b in (_item_brief(it) for it in data[:_TOP_ITEMS]
                                        if isinstance(it, dict)) if b["name"]]
        return out
    if isinstance(data, str):
        out["chars"] = len(data)
        out["preview"] = data[:_PREVIEW]
        return out
    # 结构化对象（画像/行程/提案文档等）：逐键浅压缩，长文本自动折为 {len,preview}
    if isinstance(data, dict):
        out["fields"] = {k: _brief(v) for k, v in list(data.items())[:12]}
    else:
        out["result"] = _brief(data)
    return out


# 工具失败时提供可操作路径；是否提问由关键缺口决定，不强制以追问结尾。
_DEGRADED_FOLLOWUP = INTERVIEW_DEGRADED_GUIDANCE


def _tool_content(name: str, env: Envelope) -> str:
    """摘要序列化为 JSON 字符串并截到安全上限（R5）；失败/降级附追问指令。"""
    if name == "search_literature":
        summary = _literature_summary(env)
        summary["instruction"] = (
            "只依据items中的标题/摘要节选初筛，不代表读过全文；数据库命中数不是已取回数。"
            "先按query_reports调整不足路线；已覆盖问题或预算用尽就停止，通常推荐5-8篇。"
            "比较解释与材料缺口，必要时将候选问题/文献局限写入research_notes并附真实来源；"
            "用户采纳后才改research_questions。禁止用命中数断言研究空白。")
        dumped = json.dumps(summary, ensure_ascii=False, default=str)
        # 保持JSON完整；按整条候选缩减，不在DOI/作者/JSON语法中间截断。
        while len(dumped) > _LITERATURE_CONTENT_LIMIT and summary.get("items"):
            summary["items"].pop()
            summary["summarized_count"] = len(summary["items"])
            dumped = json.dumps(summary, ensure_ascii=False, default=str)
        if len(dumped) > _LITERATURE_CONTENT_LIMIT:
            # 极端畸形上游响应仍有界，保留可操作错误，不输出半截JSON。
            dumped = json.dumps({"tool": name, "ok": False, "error": {
                "code": "literature_summary_too_large", "message": "检索元数据过长，请缩小矩阵。"}},
                ensure_ascii=False)
        return dumped
    from fieldwork_agent.runtime.travel_summary import TRAVEL_TOOLS, bounded_json
    summary = _summarize(name, env)
    if env.degraded or not env.ok:
        summary["instruction"] = _DEGRADED_FOLLOWUP
    return bounded_json(summary, 6000 if name in TRAVEL_TOOLS else TOOL_CONTENT_LIMIT)


def _coerce_args(raw: Any) -> dict:
    """tool_call 参数容错：已是 dict 直接用；JSON 字符串则解析；否则空。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _assistant_message(resp: dict) -> dict:
    """把带 tool_calls 的响应回写成 OpenAI 形态的 assistant 消息（维持 API 上下文合法）。"""
    calls = []
    for c in resp.get("tool_calls") or []:
        calls.append({
            "id": c.get("id", ""),
            "type": "function",
            "function": {
                "name": c.get("name", ""),
                "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False),
            },
        })
    return {"role": "assistant", "content": resp.get("content", "") or "",
            "tool_calls": calls}


def _budget_skip_content() -> str:
    """被额度截断的 tool_call 的 role=tool 回填内容（C3：id 必须有对应消息）。"""
    return json.dumps({"error": "budget_exceeded",
                       "message": "本回合工具调用额度已用尽，该调用未执行"},
                      ensure_ascii=False)


def run_tool_loop(llm: Any, messages: list[dict],
                  dispatch: Callable[[str, dict], Envelope], tools: list[dict], *,
                  max_iters: int = 8, slot: str = "interview",
                  on_event: Callable[[str, dict], None] | None = None,
                  max_tool_calls: int = 20, max_wall_seconds: float = 60.0) -> LoopResult:
    """共享工具回合循环。

    ①``resp = llm.chat(messages, tools=tools, slot=slot)``；②无 ``tool_calls`` →
    直接收敛返回；③每条 call 先 ``tool_started`` 事件、``dispatch``、结构化摘要回填
    ``role=tool`` 消息、再 ``tool_completed`` 事件；④任一预算上限命中则追加 system
    提示后取一次最终回答再返回。``messages`` 为入参副本，不改动调用方列表。
    """
    msgs = list(messages)
    from fieldwork_agent.kernel.source_requests import SourceBudget
    budget = getattr(dispatch, "_source_budget", None)
    if budget is None:
        dispatch = SourceBudget(deadline=time.monotonic() + max_wall_seconds).wrap(dispatch)
    else:
        budget.deadline = min(budget.deadline, time.monotonic() + max_wall_seconds)
    dispatch = LiteratureSearchBudget().wrap(dispatch)
    tool_events: list[dict] = []
    iterations = 0
    total_calls = 0
    start = time.monotonic()
    limit_reason: str | None = None

    while True:
        if iterations >= max_iters:
            limit_reason = "迭代已达上限，请基于已有信息回答"
            break
        resp = llm.chat(msgs, tools=tools, slot=slot)
        iterations += 1
        calls = resp.get("tool_calls")
        if not calls:
            return LoopResult(reply=resp.get("content", "") or "",
                              tool_events=tool_events, iterations=iterations,
                              stopped_by_limit=False)
        msgs.append(_assistant_message(resp))
        remaining = max_tool_calls - total_calls
        executed, skipped = calls[:remaining], calls[remaining:]
        for call in skipped:            # 先回填被跳过者（协议 C3），再执行
            msgs.append({"role": "tool", "tool_call_id": call.get("id", ""),
                         "content": _budget_skip_content()})
            tool_events.append({"tool": call.get("name", ""),
                                "call_id": call.get("id", ""), "ok": False,
                                "data": None, "degraded": None,
                                "arguments": _coerce_args(call.get("arguments")),
                                "error": ToolError(code="budget_exceeded",
                                                   message="本回合工具调用额度已用尽，该调用未执行")})
            if on_event:
                on_event("tool_completed", {"call_id": call.get("id", ""), "ok": False,
                                            "data": None, "degraded": None,
                                            "error": {"code": "budget_exceeded",
                                                      "message": "本回合工具调用额度已用尽，该调用未执行"}})
        for call in executed:
            name = call.get("name", "")
            call_id = call.get("id", "")
            args = _coerce_args(call.get("arguments"))
            if on_event:
                on_event("tool_started", {"call_id": call_id, "name": name,
                                          "arguments": args})
            env = dispatch(name, args)
            total_calls += 1
            msgs.append({"role": "tool", "tool_call_id": call_id,
                         "content": _tool_content(name, env)})
            tool_events.append({"tool": name, "call_id": call_id, "ok": env.ok,
                                "data": env.data, "degraded": env.degraded,
                                "arguments": args, "source_usage": env.usage})
            if on_event:
                on_event("tool_completed", {"call_id": call_id, "ok": env.ok,
                                            "data": env.data, "degraded": env.degraded,
                                            "error": _error_dict(env)})
        if skipped:
            limit_reason = "工具调用次数已达上限，请基于已有信息回答"
            break
        if time.monotonic() - start > max_wall_seconds:
            limit_reason = "回合时长已达上限，请基于已有信息回答"
            break
        if total_calls >= max_tool_calls:
            limit_reason = "工具调用次数已达上限，请基于已有信息回答"
            break

    # 触顶收敛：追加 system 提示后再取一次最终回答（不再执行新的 tool_calls）
    msgs.append({"role": "system", "content": limit_reason})
    resp = llm.chat(msgs, tools=tools, slot=slot)
    iterations += 1
    reply = resp.get("content", "") or ""
    if not reply:
        reply = "（已达回合上限，未能基于现有信息给出完整回答）"
    return LoopResult(reply=reply, tool_events=tool_events, iterations=iterations,
                      stopped_by_limit=True)
