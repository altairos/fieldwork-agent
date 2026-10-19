"""交互约定 prompt 契约（问题①修复）：多组带标签 [[options:标签]]、一问一组。

根因：旧 prompt 让模型「一次只问 2-3 个问题」，但快捷回复只支持单组
[[options]]，导致多问时选项只覆盖其中一个问题。修复后交互约定要求「每个
可枚举问题各自配一组带标签选项块、一问一组一一对应」，与多组抽取契约对齐。
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
    """研究定位阶段仍保留「一次只问 2-3 个问题」节奏，但明确要求一问一组配选项，
    消除「多问 vs 单组选项」矛盾（问题①根因）。"""
    phase = phases.phase_prompt("research_positioning")
    assert "一次只问 2-3 个问题" in phase      # 节奏保留
    assert "一问一组" in phase                 # 与选项契约和解
