"""宿主层受限状态写工具（单一实现，web/CLI 共用；F02 抽取自 web_host）。

校验规则唯一来源 kernel/context_patch.py（Web 回合、CLI、MCP 三入口一致）。
绑定单一项目 pid，每回合/会话构建，不入全局 registry——kernel 能力保持
只读，状态写只在宿主层发生。任一字段非法即整次拒绝（原子语义）。
F05：注入 TurnPatchBuffer 时写操作进暂存区（轮末 commit/取消 discard），
校验读 staged_view 合并视图（NA 存量豁免链不断）；缺省 None 行为不变。
"""
from __future__ import annotations

from fieldwork_agent.kernel.context_patch import (
    context_fields_hint,
    sanitize_context_patch,
)
from fieldwork_agent.kernel.models import Envelope, ToolError

_UPDATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_research_context",
        "description": (
            "把对话中用户明确确认的研究信息写入项目状态（仅白名单字段）。"
            "禁止推测、禁止补默认值；修正用完整新值覆盖（如预算 2 万改 5 万）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "object",
                    "description": "要写入的字段 → 新值；白名单外键或类型不符将整次拒绝",
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


def build_project_state_tools(repo, pid: str, *,
                              buffer: "TurnPatchBuffer | None" = None):
    """构建绑定 pid 的受限状态写工具：返回 (schemas, handlers)。

    ``buffer``（F05）注入后写操作进回合级暂存区，轮末由宿主 commit/discard。
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

    return [_UPDATE_SCHEMA], {"update_research_context": update_research_context}
