"""实际提问时才生成一问一组的快捷选项；默认一个主问，允许自然结束。"""
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
    assert "没有向用户提问就不生成选项块" in _OPTIONS_REMINDER
    assert "一个主问题" in _OPTIONS_REMINDER


def test_positioning_phase_prompt_reconciles_pacing_with_options():
    """新节奏与原有多组选项协议兼容，不为按钮强行提问。"""
    phase = phases.phase_prompt("research_positioning")
    assert "默认一个主问题" in phase
    assert "信息足够时直接交付并自然结束" in phase
    assert "一次只问 2-3 个问题" not in phase
    assert "一问一组" in phase                 # 与选项契约和解
