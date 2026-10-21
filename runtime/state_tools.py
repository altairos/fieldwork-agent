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
from fieldwork_agent.runtime.informants import (
    MAX_INFORMANTS_PER_CALL,
    register_informants,
)

_UPDATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_research_context",
        "description": (
            "保存已确认研究信息与带状态的工作笔记（仅白名单字段）。正式字段禁止推测、禁止补默认值；"
            "修正用完整新值覆盖。候选/文献缺口/否定方向写research_notes（id?、kind、content、origin、"
            "status、basis、references）；采纳/否定需用户依据，文献笔记需真实来源。"
            "当前焦点/未解决疑问/用户跳过的字段写interview_state（current_focus/open_questions/deferred_fields）。"
            "学术取向/目标院校/院系/学位/导师分别写research_orientation/target_institution/"
            "target_department/target_degree/target_advisor，与target_location田野地点分开；"
            "用户撤回学术目标时用clear_fields显式清空，禁止将推荐或歧义简称自动认定为用户选择。"),
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

# P0-2：候选只由 suggest_informants 返回、不落库；用户采纳后必须经此工具写入，
# 否则右栏「访谈对象」恒为空、B→C 阶段（confirmed_site + informant）永远停住。
# P2-1：条数与字段长度都有上限（与 runtime.informants 同一口径）——此前没有
# maxItems，探针一次写入 30 条也能成功。
_INFORMANT_FIELDS = {
    "type": {"type": "string", "maxLength": 40,
             "description": "访谈对象类型（必填，如「村干部」）"},
    "approach": {"type": "string", "maxLength": 400, "description": "接近策略"},
    "access_channel": {"type": "string", "maxLength": 400,
                       "description": "进入田野途径"},
    "preparation": {"type": "string", "maxLength": 400, "description": "访前准备"},
    "cautions": {"type": "string", "maxLength": 400, "description": "注意事项"},
    "ethics": {"type": "string", "maxLength": 400, "description": "伦理提示"},
    "question_domains": {"type": "array", "maxItems": 8,
                          "items": {"type": "string", "maxLength": 60},
                          "description": "建议提问域"},
}

_SAVE_INFORMANTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_informants",
        "description": (
            "把用户已经采纳的访谈对象候选登记进项目（逐条落库；完全相同的档案会跳过，"
            "同类型不同接近策略照常登记）。"
            "suggest_informants 只返回候选、不会保存；用户确认候选后必须调用本工具，"
            "否则右栏「访谈对象」仍为空，阶段不会推进。可直接回传 suggest_informants 的条目。"
            f"单次最多 {MAX_INFORMANTS_PER_CALL} 条。"),
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_INFORMANTS_PER_CALL,
                    "description": f"要登记的候选（InformantCandidate 字段）；1-{MAX_INFORMANTS_PER_CALL} 条",
                    "items": {"type": "object", "properties": _INFORMANT_FIELDS,
                              "required": ["type"]},
                },
                "basis": {"type": "string", "maxLength": 200,
                           "description": "用户采纳依据（如「用户说按这三类来」）"},
            },
            "required": ["candidates"],
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
            # P2-1：访谈对象暂存同样进 brief——此前只合并 research_context，
            # 本轮 save_informants 之后 brief 仍显示「访谈对象：（无）」。
            state = {**state,
                     "research_context": buffer.staged_view(),
                     "informants": [*state.get("informants", []),
                                    *buffer.staged_informants()]}
        return Envelope(ok=True,
                        data={"brief": build_project_brief(
                            state, cached_searches=repo.list_cached_searches(pid))})

    def save_informants(candidates: list, basis: str = "") -> Envelope:
        """登记用户已采纳的访谈对象候选（P0-2：候选不落库，必须显式写入）。

        P2-1：编号/去重/上限统一由 ``runtime.informants.register_informants``
        负责（与 REST 采纳同一套规则）；注入 buffer 时暂存到轮末 commit
        （取消/失败随 buffer 丢弃），否则直接落盘。全部无效/重复时返回
        ok=False，让模型知道本次没有任何写入。
        """
        if not isinstance(candidates, list) or not candidates:
            return Envelope(ok=False, error=ToolError(
                code="invalid_arguments", message="candidates 必须是非空数组",
                hint="传入至少一条 InformantCandidate（type 必填）"))
        result = register_informants(repo, pid, candidates, buffer=buffer)
        if not result["saved"]:
            return Envelope(ok=False, error=ToolError(
                code="nothing_saved",
                message="没有任何候选被登记（全部无效或已存在）",
                hint="检查 candidates 形状；完全相同的档案不会重复写入"))
        data: dict[str, Any] = {"saved": result["saved"],
                                "skipped": result["skipped"],
                                "total": result["total"]}
        if basis:
            data["basis"] = basis[:200]
        return Envelope(ok=True, data=data)

    return ([_UPDATE_SCHEMA, _BRIEF_SCHEMA, _SAVE_INFORMANTS_SCHEMA],
            {"update_research_context": update_research_context,
             "get_project_brief": get_project_brief,
             "save_informants": save_informants})
