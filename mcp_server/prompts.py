"""MCP Prompts — 2 个 prompt 模板随协议分发（spec §2.2）。

从 kernel 常量拼装，禁止复制第二份事实。
"""

from typing import Any

from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.guidance.interview import INTERVIEW_GUIDANCE, INTERVIEW_TURN_REMINDER
from fieldwork_agent.kernel.guidance.literature import LITERATURE_GUIDANCE
from fieldwork_agent.kernel.guidance.research import RESEARCH_WORKFLOW_GUIDANCE
from fieldwork_agent.kernel.tool_usage import TOOL_USAGE_GUIDANCE
from mcp.types import Prompt, PromptArgument

# ---------------------------------------------------------------- Prompt 目录

_PROMPTS: list[Prompt] = [
    Prompt(
        name="fieldwork_interview",
        description="田野访谈引导：按阶段生成系统提示词与引导问题",
        arguments=[
            PromptArgument(
                name="phase",
                description="目标阶段名（如 field_site_profiles）",
                required=True,
            ),
        ],
    ),
    Prompt(
        name="refine_proposal_section",
        description="Proposal 单章精炼：只输出指定章节的 markdown",
        arguments=[
            PromptArgument(
                name="section",
                description="章节标题（如「一、研究背景与问题提出」）",
                required=True,
            ),
        ],
    ),
]


def list_prompts() -> list[Prompt]:
    """返回全部 prompt 定义（恰 2 个）。"""
    return list(_PROMPTS)


def get_prompt(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """按名称渲染 prompt，返回 ok 包络含 description + messages。"""
    args = args or {}

    if name == "fieldwork_interview":
        phase = args.get("phase", phases.PHASES[0])
        system_text = phases.phase_prompt(phase)
        # 首行提示宿主先读资源
        content = (
            f"[提示：建议宿主先读取 fieldwork://guidance/phases 与 "
            f"fieldwork://guidance/questions/{phase} 获取完整上下文]\n\n"
            f"{system_text}\n\n{LITERATURE_GUIDANCE}\n\n{INTERVIEW_GUIDANCE}"
            f"\n\n{TOOL_USAGE_GUIDANCE}\n\n{RESEARCH_WORKFLOW_GUIDANCE}\n\n{INTERVIEW_TURN_REMINDER}"
        )
        return {
            "ok": True,
            "description": f"田野访谈引导（阶段：{phase}）",
            "messages": [{"role": "user", "content": content}],
        }

    if name == "refine_proposal_section":
        section = args.get("section", "")
        content = (
            f"请只输出以下章节的 markdown 内容，不要包含其他章节：\n\n"
            f"## {section}\n\n"
            f"要求：学术化语言、数据具体、引用可追溯。"
        )
        return {
            "ok": True,
            "description": f"Proposal 单章精炼：{section}",
            "messages": [{"role": "user", "content": content}],
        }

    # 未知 prompt
    return {
        "ok": False,
        "error": {
            "code": "prompt_not_found",
            "message": f"未知 prompt: {name}",
            "hint": "使用 list_prompts 查看可用 prompt",
        },
    }
