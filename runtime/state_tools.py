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
from fieldwork_agent.kernel.documents.informants import candidate_to_record
from fieldwork_agent.kernel.guidance.brief import build_project_brief
from fieldwork_agent.kernel.models import Envelope, InformantCandidate, ToolError
from pydantic import ValidationError

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
_INFORMANT_FIELDS = {
    "type": {"type": "string", "description": "访谈对象类型（必填，如「村干部」）"},
    "approach": {"type": "string", "description": "接近策略"},
    "access_channel": {"type": "string", "description": "进入田野途径"},
    "preparation": {"type": "string", "description": "访前准备"},
    "cautions": {"type": "string", "description": "注意事项"},
    "ethics": {"type": "string", "description": "伦理提示"},
    "question_domains": {"type": "array", "items": {"type": "string"},
                          "description": "建议提问域"},
}

_SAVE_INFORMANTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_informants",
        "description": (
            "把用户已经采纳的访谈对象候选登记进项目（逐条落库，按 type 去重）。"
            "suggest_informants 只返回候选、不会保存；用户确认候选后必须调用本工具，"
            "否则右栏「访谈对象」仍为空，阶段不会推进。可直接回传 suggest_informants 的条目。"),
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "description": "要登记的候选（InformantCandidate 字段）；至少 1 条",
                    "items": {"type": "object", "properties": _INFORMANT_FIELDS,
                              "required": ["type"]},
                },
                "basis": {"type": "string",
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
            state = {**state,
                     "research_context": buffer.staged_view()}
        return Envelope(ok=True,
                        data={"brief": build_project_brief(
                            state, cached_searches=repo.list_cached_searches(pid))})

    def save_informants(candidates: list, basis: str = "") -> Envelope:
        """登记用户已采纳的访谈对象候选（P0-2：候选不落库，必须显式写入）。

        逐条校验、按 type 去重（仓储 + 本轮暂存）；注入 buffer 时暂存到轮末
        commit（取消/失败随 buffer 丢弃），否则直接落盘。全部无效/重复时
        返回 ok=False，让模型知道本次没有任何写入。
        """
        if not isinstance(candidates, list) or not candidates:
            return Envelope(ok=False, error=ToolError(
                code="invalid_arguments", message="candidates 必须是非空数组",
                hint="传入至少一条 InformantCandidate（type 必填）"))
        records_now = repo.list_informants(pid)
        staged = buffer.staged_informants() if buffer is not None else []
        seen = {str(r.get("type") or "").strip() for r in [*records_now, *staged]}
        seq = len(records_now) + len(staged) + 1
        saved: list[dict] = []
        skipped: list[dict] = []
        for raw in candidates:
            try:
                candidate = InformantCandidate.model_validate(raw)
            except ValidationError as exc:
                skipped.append({"type": "", "reason": "invalid",
                                "message": exc.errors()[0].get("msg", "")})
                continue
            candidate.type = candidate.type.strip()
            if not candidate.type:
                skipped.append({"type": "", "reason": "missing_type"})
                continue
            if candidate.type in seen:
                skipped.append({"type": candidate.type, "reason": "duplicate"})
                continue
            record = candidate_to_record(candidate, seq)
            seq += 1
            if buffer is not None:
                buffer.stage_informant(record)
            else:
                repo.save_informant(pid, record)
            seen.add(candidate.type)
            saved.append({"code": record["code"], "type": record["type"]})
        if not saved:
            return Envelope(ok=False, error=ToolError(
                code="nothing_saved",
                message="没有任何候选被登记（全部无效或已存在）",
                hint="检查 candidates 形状；已登记的 type 不会重复写入"))
        data: dict[str, Any] = {"saved": saved, "skipped": skipped,
                                "total": len(records_now) + len(staged) + len(saved)}
        if basis:
            data["basis"] = basis
        return Envelope(ok=True, data=data)

    return ([_UPDATE_SCHEMA, _BRIEF_SCHEMA, _SAVE_INFORMANTS_SCHEMA],
            {"update_research_context": update_research_context,
             "get_project_brief": get_project_brief,
             "save_informants": save_informants})
