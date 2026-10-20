"""交互约定与提问节奏（合并版）：多组带标签 [[options:标签]]、一问一组；
需要追问时默认一个主问题、信息足够时直接交付并自然结束。

问题①根因：旧 prompt「一次只问 2-3 个问题」与快捷回复单组 [[options]]
冲突，多问时选项只覆盖一问。合并版 prompt 以「默认一个主问题」节奏取代
多问，同时保留每个可枚举问题各自配一组带标签选项块、一问一组一一对应；不要求每轮提问。
"""
from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.web_host.agent.context import (
    _OPTIONS_REMINDER,
    build_system_prompt,
)


def test_system_prompt_describes_labeled_multi_group_options():
    """system prompt 交互约定：带标签格式 + 一问一组一一对应。"""
    prompt = build_system_prompt("research_positioning", "项目快照")
    assert "[[options:" in prompt              # 带标签格式（不再只有无标签单组）
    assert "每个可枚举问题" in prompt
    assert "一问一组" in prompt


def test_options_reminder_mentions_labeled_multi_group():
    """每轮尾部提醒同步升级为多组带标签、一问一组。"""
    assert "[[options:" in _OPTIONS_REMINDER
    assert "一问一组" in _OPTIONS_REMINDER
def test_positioning_phase_prompt_reconciles_pacing_with_options():
    """需要追问时默认一个主问题、一问一组配选项，直接交付时不强制提问。

    HEAD 节奏（默认一个主问题、信息足够直接交付自然结束）与分支选项契约
    （每个可枚举问题各配一组带标签 [[options:标签]]、一问一组）在合并版
    prompt 中融合共存；旧「一次只问 2-3 个问题」多问节奏不再出现。
    """
    phase = phases.phase_prompt("research_positioning")
    assert "需要追问时默认一个主问题" in phase
    assert "信息足够时直接交付并自然结束" in phase
    assert "一次只问 2-3 个问题" not in phase
    assert "一问一组" in phase                 # 与选项契约和解
