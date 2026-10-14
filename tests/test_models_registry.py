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
