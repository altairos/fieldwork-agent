"""kernel 数据契约与工具 schema 单一来源 — models / registry。"""
import pytest
from fieldwork_agent.kernel import registry
from fieldwork_agent.kernel.models import Envelope, ResearchContext, SearchItem, ToolError
from fieldwork_agent.kernel.registry import CapabilityMeta, build_tool_schemas, register
from pydantic import BaseModel


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """每个用例独享一份注册表，避免 demo 能力泄漏到其他测试文件。"""
    monkeypatch.setattr(registry, "_REGISTRY", {})


def test_envelope_defaults():
    env = Envelope(ok=False, error=ToolError(code="flyai_unavailable", message="no cli"))
    assert env.ok is False and env.data is None and env.degraded is None


def test_searchitem_price_is_float():
    assert SearchItem(name="X", price=1234.0).price == 1234.0


def test_research_context_defaults_empty():
    ctx = ResearchContext()
    assert ctx.discipline == "" and ctx.extras == {}


def test_build_tool_schemas_openai_format():
    class PingArgs(BaseModel):
        msg: str
        limit: int = 3

    def _ping(msg: str, limit: int = 3) -> Envelope:
        return Envelope(ok=True, data=msg)

    register(CapabilityMeta(name="demo_ping", description="demo tool",
                           fn=_ping, args_model=PingArgs))
    schemas = build_tool_schemas()
    tool = [t for t in schemas if t["function"]["name"] == "demo_ping"][0]
    props = tool["function"]["parameters"]["properties"]
    assert "msg" in props and props["limit"]["default"] == 3
    assert tool["function"]["parameters"].get("required") == ["msg"]


# ---------------------------------------------------------------- 契约修订（审阅意见 R1/R2/R3）


def test_budgetitem_provenance_defaults():
    from fieldwork_agent.kernel.models import BudgetItem

    item = BudgetItem(name="住宿", amount=1400.0)
    assert item.currency == "CNY"
    assert item.unit == "" and item.quantity == 1.0 and item.people == 1
    assert item.priced_at == "" and item.source_ref == ""


def test_budgetbreakdown_coverage_semantics():
    from fieldwork_agent.kernel.models import BudgetBreakdown

    b = BudgetBreakdown()
    assert b.coverage == "unknown" and b.uncovered == [] and b.over_budget is False


def test_proposal_doc_modes():
    from fieldwork_agent.kernel.models import ProposalDoc

    doc = ProposalDoc()
    assert doc.requested_mode == "global" and doc.mode == "global"
    assert doc.inputs_hash == ""


def test_confirmation_record_and_staleness():
    from fieldwork_agent.kernel.models import (
        ConfirmationRecord,
        ItineraryPlan,
        inputs_stale,
    )

    assert ItineraryPlan().inputs_hash == ""
    rec = ConfirmationRecord(kind="proposal", subject="p1", confirmed_at="2026-10-14",
                             inputs_hash="abc")
    assert rec.inputs_hash == "abc"
    assert inputs_stale("abc", "abd") is True
    assert inputs_stale("abc", "abc") is False


def test_compute_inputs_hash_stable():
    from fieldwork_agent.kernel.models import compute_inputs_hash

    h1 = compute_inputs_hash({"date": "2026-11-01", "site": "大理", "people": 1})
    h2 = compute_inputs_hash({"people": 1, "site": "大理", "date": "2026-11-01"})
    assert h1 == h2 and len(h1) == 40  # sha1 hex；键序无关


def test_informant_candidate():
    from fieldwork_agent.kernel.models import InformantCandidate

    c = InformantCandidate(type="社群骨干", approach="a", cautions="c",
                           question_domains=["q1"], ethics="e",
                           access_channel="ac", preparation="p")
    assert c.type == "社群骨干" and len(c.question_domains) == 1


def test_research_context_purpose_and_na():
    ctx = ResearchContext()
    assert ctx.purpose == "" and ctx.interviews_not_applicable is False
    ctx2 = ResearchContext(purpose="global", interviews_not_applicable=True,
                           interviews_na_reason="档案研究")
    assert ctx2.purpose == "global" and ctx2.interviews_na_reason == "档案研究"


def test_summary_hints_field():
    from fieldwork_agent.kernel.models import SummaryHints

    assert SummaryHints().hints == {}
