"""预算契约区间字段测试：旧 payload 可反序列化，新字段默认空/False。"""

from fieldwork_agent.kernel.models import BudgetBreakdown, BudgetItem


def test_budget_item_range_fields_default_none():
    item = BudgetItem(name="交通", amount=350.0, source="cheapest")
    assert item.amount_low is None
    assert item.amount_high is None
    assert item.basis == ""


def test_budget_breakdown_range_fields_default():
    b = BudgetBreakdown(total=100.0, budget_total=200.0, coverage="complete")
    assert b.total_high is None
    assert b.over_budget_possible is False


def test_old_payload_deserializes():
    b = BudgetBreakdown.model_validate({
        "items": [{"name": "住宿", "amount": 13500}],
        "total": 13500, "budget_total": 20000,
        "coverage": "partial", "uncovered": ["餐饮"],
    })
    assert b.items[0].amount_high is None
    assert b.total_high is None and b.over_budget_possible is False
