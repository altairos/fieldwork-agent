"""文档工具入参合并：以仓储已采纳状态为事实来源（F01 / 评审 R02）。

宿主在工具分派前调用 merge_doc_args：文档工具（compose_proposal /
compose_interview_guide）的入参中，模型没带或给空的槽位由仓储
ResearchContext 补齐；模型本轮聚焦的非空值保留。runtime 依赖 kernel、
由各宿主（web/CLI/MCP）自行挂接，kernel 保持无状态。

``ctx_view``（F05×F01，终审 M1）：宿主可传"仓储+回合暂存"合并视图
（web 的 TurnPatchBuffer.staged_view()）——同轮先 update_research_context
再调文档工具时立即看到新值；缺省 None 直读仓储（CLI/MCP 行为不变）。

**四路事实渠道（评审 R02）**：ctx 之外的田野点 / 报道人 / 知识库 / 行程是
项目记录集合，只能以「项目已采纳状态」为正文依据——不接受模型自造的替代值。
否则正文用了 B、项目仍是 A，五路指纹却按项目现读值签名，确认依然通过。
已采纳状态为空的渠道（如尚未排行程）仍保留模型值：这类草稿不计为已采纳，
落盘指纹会与确认时的项目状态不一致，因而无法认证为有效方案。
"""
from __future__ import annotations

import copy
import json
from typing import Any

_DOC_TOOLS = ("compose_proposal", "compose_interview_guide")
# ctx 之外的事实渠道：正文只能引用项目已采纳的记录
_FACT_CHANNELS = ("sites", "informants", "knowledge", "itinerary")
# web 宿主下发给模型的 schema 里不暴露的字段：全部由 merge_doc_args 从项目
# 状态填充，模型回传只会制造巨型参数与 invalid_arguments 重试（生产实录）。
_DOC_HOST_FILLED_FIELDS = {
    "compose_proposal": ("ctx", "sites", "itinerary", "informants", "knowledge"),
}


def compact_doc_tool_schemas(schemas: list[dict]) -> list[dict]:
    """返回副本：隐藏文档工具的宿主填充字段，并在描述里声明免传。

    仅 web 宿主使用（其 ``_prepare`` 必调 ``merge_doc_args``）；MCP 外部客户端
    无此合并路径，继续使用 registry 原始 schema，故不得就地修改入参。
    """
    out: list[dict] = []
    for schema in schemas:
        fn = schema.get("function") or {}
        hidden = _DOC_HOST_FILLED_FIELDS.get(fn.get("name", ""))
        if not hidden:
            out.append(schema)
            continue
        clone = copy.deepcopy(schema)
        params = clone["function"].setdefault("parameters", {})
        props = params.get("properties") or {}
        for field in hidden:
            props.pop(field, None)
        clone["function"]["description"] = (
            clone["function"].get("description", "")
            + "；上下文渠道（研究定位/田野点/行程/报道人/知识库）由宿主从项目"
              "状态自动填充，不要传入。")
        out.append(clone)
    return out


def _nonempty(v: Any) -> bool:
    return isinstance(v, str) and v.strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def accepted_doc_inputs(repo: Any, pid: str, *, ctx_view: dict | None = None,
                        itinerary_override: dict | None = None) -> dict:
    """项目已采纳的五渠道事实视图（方案正文的事实来源，评审 R02）。

    - ``ctx``：``ctx_view``（仓储 + 回合暂存）优先，缺省直读仓储；
    - ``sites`` / ``informants`` / ``knowledge``：仓储记录全量（顺序即采纳顺序）；
    - ``itinerary``：最新一次 ``compose_itinerary`` 快照的 result；
    - ``itinerary_cid``：该快照 id（无行程时为空串）。

    ``itinerary_override``：宿主传入的本回合已成功产生、但尚未落盘的行程
    （评审 T01）。同轮“先排行程 → 写方案”时正文必须采用这份行程——否则
    正文用模型给的 B、轮末却把落盘行程换成 A，指纹与正文错配。传入非 None
    即优先于仓储快照（此时 ``itinerary_cid`` 为空串：快照尚未落盘）。
    """
    cid = ""
    itinerary = itinerary_override
    if itinerary is None:
        for cached in reversed(repo.list_cached_searches(pid)):
            if cached.get("tool") == "compose_itinerary":
                cid = cached["id"]
                record = repo.get_cached_search(pid, cid) or {}
                itinerary = record.get("result")
                break
    return {
        "ctx": dict(ctx_view if ctx_view is not None else repo.get_research_context(pid)),
        "sites": list(repo.list_field_sites(pid)),
        "informants": list(repo.list_informants(pid)),
        "knowledge": list(repo.list_knowledge(pid)),
        "itinerary": itinerary,
        "itinerary_cid": cid,
    }


def _merge_proposal_ctx(base_ctx: dict, given_ctx: Any) -> dict[str, Any]:
    """proposal 的 ctx：base_ctx 为底，模型值覆盖；extras 键级合并。"""
    base = dict(base_ctx)
    given = given_ctx if isinstance(given_ctx, dict) else {}
    for key, value in given.items():
        if key == "extras":
            merged_extras = dict(base.get("extras") or {})
            if isinstance(value, dict):
                merged_extras.update(value)
            base["extras"] = merged_extras
        elif _nonempty(value):
            base[key] = value
    return base


def _apply_accepted_channels(args: dict, accepted: dict) -> list[str]:
    """四路事实渠道改用项目已采纳状态；返回被替换（模型值与采纳值不同）的渠道名。

    采纳状态为空时保留模型值——不静默丢弃模型输入，但落盘指纹会与项目状态
    不符（行程为空则 itinerary 记为 None），确认时被拒而不是误认。
    """
    replaced: list[str] = []
    for channel in _FACT_CHANNELS:
        accepted_value = accepted.get(channel)
        if not accepted_value:
            continue                      # 项目侧暂无该渠道：保留模型值（不入指纹）
        if _canonical(args.get(channel)) != _canonical(accepted_value):
            replaced.append(channel)
        args[channel] = accepted_value if channel == "itinerary" else list(accepted_value)
    return replaced


def merge_doc_args(repo: Any, pid: str, name: str, args: dict,
                   ctx_view: dict | None = None, *,
                   accepted: dict | None = None,
                   replaced: list[str] | None = None) -> dict:
    """非文档工具原样返回；文档工具以 ctx_view（缺省仓储）为底合并模型值
    （就地改并返回 args）。

    ``accepted``：项目已采纳的五渠道视图（``accepted_doc_inputs`` 的结果）；
    缺省时按需现读。``replaced``：被采纳值替换掉的渠道名收集到该列表
    （供宿主在工具结果里如实标注）。
    """
    if name not in _DOC_TOOLS:
        return args
    base_ctx = (ctx_view if ctx_view is not None
                else repo.get_research_context(pid))
    if name == "compose_proposal":
        args["ctx"] = _merge_proposal_ctx(base_ctx, args.get("ctx"))
        view = accepted if accepted is not None else accepted_doc_inputs(repo, pid)
        hit = _apply_accepted_channels(args, view)
        if replaced is not None:
            replaced.extend(hit)
        return args
    # compose_interview_guide：仓储槽位补空值，模型聚焦（非空）保留
    for key in ("topic", "research_questions", "discipline"):
        if not _nonempty(args.get(key)) and _nonempty(base_ctx.get(key)):
            args[key] = base_ctx[key]
    return args


def doc_input_fingerprint(args: dict) -> dict:
    """方案正文五渠道生成输入（落盘指纹依据，评审 R02）。

    ctx 为合并后的生成 ctx；四路事实渠道为正文实际采用的记录（已采纳状态优先，
    项目侧为空时保留模型值）。行程取内容而不取快照 id——同轮多份行程时
    指纹绑定正文用的那一份，新增/替换行程也能被判为依赖变化。
    """
    return {
        "ctx": dict(args.get("ctx") or {}),
        "sites": list(args.get("sites") or []),
        "informants": list(args.get("informants") or []),
        "knowledge": list(args.get("knowledge") or []),
        "itinerary": args.get("itinerary"),
    }
