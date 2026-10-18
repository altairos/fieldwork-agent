"""今日日期锚点测试：system 最前段必须给出「今天」，防模型给过去日期。"""

from datetime import date

from fieldwork_agent.kernel.guidance.phases import time_anchor


def test_anchor_states_today_and_weekday():
    text = time_anchor(date(2026, 10, 18))
    assert "今天是 2026-10-18（周日）" in text
    assert "过去日期" in text and "flyai_invalid_date" in text


def test_anchor_defaults_to_real_today():
    text = time_anchor()
    assert text.startswith("今天是 ")
    assert str(date.today()) in text


def test_web_and_cli_prompts_carry_anchor():
    from fieldwork_agent.core.orchestrator import _system_prompt
    from fieldwork_agent.web_host.agent.context import build_system_prompt

    for prompt in (build_system_prompt("research_positioning", "快照"),
                   _system_prompt("research_positioning", "快照")):
        assert "今天是 " in prompt
        assert "出发日期" in prompt
