# web_host/agent/state_tools.py
"""Web 回合受限状态写工具（审计 F03 方案②）：update_research_context。

与 mcp_server/state_tools.py 同构的宿主层工具：字段白名单 + 值校验 +
Envelope 包络；区别在于绑定单一项目 pid（loop 每回合构建，不入全局
registry——kernel 能力保持只读，状态写只在宿主层发生）。
"""
from fieldwork_agent.kernel.models import Envelope, ToolError

ALLOWED_CONTEXT_FIELDS = (
    "discipline", "topic", "site_status", "scale", "budget",
    "time_window", "departure_city", "target_location", "travel_date",
    "purpose", "interviews_not_applicable", "interviews_na_reason",
)

_SCHEMA = {
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
                    "description": "要写入的字段 → 新值（白名单外键静默忽略）",
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


def build_state_tools(repo, pid: str):
    """构建绑定 pid 的受限状态写工具：返回 (schemas, handlers)。"""

    def update_research_context(patch: dict, reason: str = "") -> Envelope:
        clean = {}
        for key, value in (patch or {}).items():
            if key not in ALLOWED_CONTEXT_FIELDS:
                continue
            if isinstance(value, bool):
                clean[key] = value
            elif isinstance(value, str) and value.strip():
                clean[key] = value.strip()
        if not clean:
            return Envelope(ok=False, error=ToolError(
                code="invalid_arguments",
                message="patch 为空：至少一个白名单字段且值为非空字符串或布尔",
                hint="可写白名单：" + "、".join(ALLOWED_CONTEXT_FIELDS)))
        ctx = dict(repo.get_research_context(pid))
        ctx.update(clean)
        repo.update_research_context(pid, ctx)
        return Envelope(ok=True, data={"updated": sorted(clean)})

    return [_SCHEMA], {"update_research_context": update_research_context}
