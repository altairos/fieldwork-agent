"""文档工具入参合并：以仓储研究上下文为底、模型值叠加（F01）。

宿主在工具分派前调用 merge_doc_args：文档工具（compose_proposal /
compose_interview_guide）的入参中，模型没带或给空的槽位由仓储
ResearchContext 补齐；模型本轮聚焦的非空值保留。runtime 依赖 kernel、
由各宿主（web/CLI/MCP）自行挂接，kernel 保持无状态。

``ctx_view``（F05×F01，终审 M1）：宿主可传"仓储+回合暂存"合并视图
（web 的 TurnPatchBuffer.staged_view()）——同轮先 update_research_context
再调文档工具时立即看到新值；缺省 None 直读仓储（CLI/MCP 行为不变）。
"""
from __future__ import annotations

from typing import Any

_DOC_TOOLS = ("compose_proposal", "compose_interview_guide")


def _nonempty(v: Any) -> bool:
    return isinstance(v, str) and v.strip()


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


def merge_doc_args(repo: Any, pid: str, name: str, args: dict,
                   ctx_view: dict | None = None) -> dict:
    """非文档工具原样返回；文档工具以 ctx_view（缺省仓储）为底合并模型值
    （就地改并返回 args）。"""
    if name not in _DOC_TOOLS:
        return args
    base_ctx = (ctx_view if ctx_view is not None
                else repo.get_research_context(pid))
    if name == "compose_proposal":
        args["ctx"] = _merge_proposal_ctx(base_ctx, args.get("ctx"))
        return args
    # compose_interview_guide：仓储槽位补空值，模型聚焦（非空）保留
    for key in ("topic", "research_questions", "discipline"):
        if not _nonempty(args.get(key)) and _nonempty(base_ctx.get(key)):
            args[key] = base_ctx[key]
    return args
