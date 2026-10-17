# web_host/agent/state_tools.py
"""Web 回合受限状态写工具（审计 F03 方案② / 审计二 R05）：update_research_context。

与 mcp_server/state_tools.py 同构的宿主层工具：校验规则统一来自
kernel/context_patch.py（单一来源，三入口一致）；区别在于绑定单一项目
pid（loop 每回合构建，不入全局 registry——kernel 能力保持只读，状态写
只在宿主层发生）。任一字段非法即整次拒绝（原子语义），绝不部分写入。
"""
from fieldwork_agent.kernel.context_patch import (
    context_fields_hint,
    sanitize_context_patch,
)
from fieldwork_agent.kernel.models import Envelope, ToolError

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


def build_state_tools(repo, pid: str):
    """构建绑定 pid 的受限状态写工具：返回 (schemas, handlers)。"""

    def update_research_context(patch: dict, reason: str = "") -> Envelope:
        try:
            clean = sanitize_context_patch(
                patch, current=repo.get_research_context(pid))
        except ValueError as exc:
            return Envelope(ok=False, error=ToolError(
                code="invalid_arguments", message=str(exc),
                hint="可写白名单：" + context_fields_hint()))
        repo.update_research_context(pid, clean)
        # 轮 2 症状②：reason 一并放进 data —— 前端 ToolCard 只消费 Envelope.data
        # （Timeline 渲染不传 args），依据文本只能经此通道下发给状态卡；缺省省略键。
        data = {"updated": sorted(clean)}
        if reason:
            data["reason"] = reason
        return Envelope(ok=True, data=data)

    return [_SCHEMA], {"update_research_context": update_research_context}
