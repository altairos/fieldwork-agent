"""宿主层受限状态写工具（单一实现，web/CLI 共用；F02 抽取自 web_host）。

校验规则唯一来源 kernel/context_patch.py（Web 回合、CLI、MCP 三入口一致）。
绑定单一项目 pid，每回合/会话构建，不入全局 registry——kernel 能力保持
只读，状态写只在宿主层发生。任一字段非法即整次拒绝（原子语义）。
F05：注入 TurnPatchBuffer 时写操作进暂存区（轮末 commit/取消 discard），
校验读 staged_view 合并视图（NA 存量豁免链不断）；缺省 None 行为不变。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fieldwork_agent.runtime.turn_buffer import TurnPatchBuffer

from fieldwork_agent.kernel.context_patch import (
    context_fields_hint,
    context_patch_schema,
    sanitize_context_patch,
)
from fieldwork_agent.kernel.guidance.brief import build_project_brief
from fieldwork_agent.kernel.models import Envelope, ToolError

_UPDATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_research_context",
        "description": (
            "保存已确认研究信息与带状态的工作笔记（仅白名单字段）。正式字段禁止推测、禁止补默认值；"
            "修正用完整新值覆盖。候选/文献缺口/否定方向写research_notes（id?、kind、content、origin、"
            "status、basis、references）；采纳/否定需用户依据，文献笔记需真实来源。"
            "当前焦点/未解决疑问/用户跳过的字段写interview_state（current_focus/open_questions/deferred_fields）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {
                    **context_patch_schema(),
                    "description": "正式字段→新值；research_notes为笔记增量列表（已有id可改状态），"
                                   "interview_state为对话记忆对象；候选不自动变成已确认问题。非法项整次拒绝",
                },
                "reason": {
                    "type": "string",
                    "description": "一句话说明依据（如「用户更正预算为 5 万」）",
                },
            },
            "required": ["patch"],
        },
    },
}

_BRIEF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_project_brief",
        "description": "查询当前项目的研究定位/田野点/访谈对象/方案/搜索缓存有界摘要",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


def build_project_state_tools(repo, pid: str, *,
                              buffer: TurnPatchBuffer | None = None):
    """构建绑定 pid 的受限状态写工具：返回 (schemas, handlers)。

    ``buffer``（F05）注入后写操作进回合级暂存区，轮末由宿主 commit/discard。
    F04：另含只读 get_project_brief——有界摘要经 kernel.guidance.brief 推导，
    暂存视图覆盖 research_context（未提交的状态也能被摘要看见）。
    """

    def update_research_context(patch: dict, reason: str = "") -> Envelope:
        try:
            clean = sanitize_context_patch(
                patch, current=(buffer.staged_view() if buffer is not None
                                else repo.get_research_context(pid)))
        except ValueError as exc:
            return Envelope(ok=False, error=ToolError(
                code="invalid_arguments", message=str(exc),
                hint="可写白名单：" + context_fields_hint()))
        (buffer.stage(clean) if buffer is not None
         else repo.update_research_context(pid, clean))
        data = {"updated": sorted(clean)}
        if reason:
            data["reason"] = reason
        return Envelope(ok=True, data=data)

    def get_project_brief(**_: Any) -> Envelope:
        state = repo.get_state(pid)
        if buffer is not None:
            state = {**state,
                     "research_context": buffer.staged_view()}
        return Envelope(ok=True,
                        data={"brief": build_project_brief(
                            state, cached_searches=repo.list_cached_searches(pid))})

    return ([_UPDATE_SCHEMA, _BRIEF_SCHEMA],
            {"update_research_context": update_research_context,
             "get_project_brief": get_project_brief})
