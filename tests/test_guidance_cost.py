"""成本口径提示词测试：单一来源接入 web 与 CLI 两处 system。"""

from fieldwork_agent.kernel.guidance.costs import COST_GUIDANCE


def test_cost_guidance_names_capability_and_contract():
    assert "estimate_living_cost" in COST_GUIDANCE
    assert "区间" in COST_GUIDANCE and "待核验" in COST_GUIDANCE
    assert "城市差旅" in COST_GUIDANCE


def test_web_system_prompt_includes_cost_guidance():
    from fieldwork_agent.web_host.agent.context import build_system_prompt

    prompt = build_system_prompt("research_positioning", "项目快照")
    assert COST_GUIDANCE in prompt


def test_cli_system_prompt_includes_cost_guidance():
    from fieldwork_agent.core.orchestrator import _system_prompt

    assert COST_GUIDANCE in _system_prompt("research_positioning", "项目快照")
