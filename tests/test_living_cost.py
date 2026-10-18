# tests/test_living_cost.py
from fieldwork_agent.kernel.aggregation.living_cost import estimate_living_cost
from fieldwork_agent.kernel.registry import build_tool_schemas


def test_village_self_cook_sixty_days():
    env = estimate_living_cost("village", "self_cook", days=60)
    assert env.ok is True
    items = {i["name"]: i for i in env.data["items"]}
    assert items["餐饮"]["amount_low"] == 12.0 * 60
    assert items["餐饮"]["amount_high"] == 20.0 * 60
    assert items["市内交通"]["amount_low"] == 0.0
    assert items["市内交通"]["amount_high"] == 5.0 * 60
    assert env.data["total_low"] == 720.0
    assert env.data["total_high"] == 1500.0
    assert env.data["caliber"] == "村内自炊 · 1 人 × 60 天"
    assert "14.7" in env.data["basis"]
    assert env.data["coverage"] == "estimate" and env.data["source"] == "estimate"


def test_transport_can_be_excluded():
    env = estimate_living_cost("county", "eat_out", days=10,
                               include_local_transport=False)
    assert [i["name"] for i in env.data["items"]] == ["餐饮"]
    assert env.data["total_low"] == env.data["items"][0]["amount_low"]


def test_people_multiplies_linearly():
    one = estimate_living_cost("village", "mixed", days=30, people=1)
    three = estimate_living_cost("village", "mixed", days=30, people=3)
    assert three.data["total_low"] == round(one.data["total_low"] * 3, 2)
    assert three.data["total_high"] == round(one.data["total_high"] * 3, 2)


def test_enums_and_bounds_rejected():
    cases = [
        {"settlement_type": "hamlet"},
        {"board_mode": "delivery"},
        {"days": 0},
        {"days": 366},
        {"people": 0},
        {"people": 21},
    ]
    base = {"days": 10}
    for extra in cases:
        env = estimate_living_cost(**{**base, **extra})
        assert env.ok is False
        assert env.error.code == "invalid_arguments"
        assert env.error.hint and "village" in env.error.hint


def test_city_eat_out_far_above_village_self_cook():
    cheap = estimate_living_cost("village", "self_cook", days=10)
    dear = estimate_living_cost("city", "eat_out", days=10)
    assert dear.data["total_low"] > cheap.data["total_high"]


def test_registered_schema_without_injection_params():
    schema = {t["function"]["name"]: t["function"] for t in build_tool_schemas()}
    assert "estimate_living_cost" in schema
    props = schema["estimate_living_cost"]["parameters"]["properties"]
    assert "client" not in props and "llm" not in props
    assert props["settlement_type"]["default"] == "village"
