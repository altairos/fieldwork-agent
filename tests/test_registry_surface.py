"""生产态能力面契约 — 裸导入即注册（验收门槛 #3）+ registry handler 统一包络（spec §2.4）。

本文件**刻意不加** autouse 的 ``_isolated_registry`` fixture（那是
``test_models_registry.py`` 的模块内 fixture，用于隔离 demo 能力），因此这里断言的是
未被 monkeypatch 的**全局真实注册表**：宿主仅
``from fieldwork_agent.kernel import build_tool_schemas``（不 import 任何子模块）
就必须看到全部 13 项能力——即验收命令
``python -c "from fieldwork_agent.kernel import build_tool_schemas; print(len(build_tool_schemas()))"``
输出 13 的测试化钉子。
"""
from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.documents.proposal import guide_handler, proposal_handler
from fieldwork_agent.kernel.models import Envelope, ProposalDoc
from fieldwork_agent.kernel.registry import capabilities

# 8 搜索原语（sources/flyai）+ 5 聚合/文档能力（aggregation ×2 / documents ×3）
_SEARCH_TOOLS = {
    "search_flights", "search_trains", "search_hotels", "search_marriott_hotels",
    "search_marriott_packages", "search_pois", "search_keyword", "search_ai",
}
_AGG_DOC_TOOLS = {
    "profile_field_site", "compose_itinerary", "compose_proposal",
    "compose_interview_guide", "suggest_informants",
}

_FULL_ARGS = {
    "ctx": {"discipline": "社会学", "topic": "宗族变迁", "target_location": "喜洲"},
    "sites": [{"name": "喜洲", "confirmed": True}],
    "itinerary": {"legs": [{"from": "昆明", "to": "大理"}]},
    "informants": [{"code": "P1", "type": "长老"}],
    "knowledge": [],
}


def _names() -> set[str]:
    return {t["function"]["name"] for t in build_tool_schemas()}


# ---------------------------------------------------------------- 裸导入即注册


def test_bare_kernel_import_yields_thirteen_schemas():
    """只靠 ``import fieldwork_agent.kernel`` 的聚合导入即得 13 个 schema（门槛 #3）。"""
    assert len(build_tool_schemas()) >= 13
    assert _SEARCH_TOOLS | _AGG_DOC_TOOLS <= _names()


def test_bare_import_sees_eight_search_and_five_aggregation_document():
    """名字集合分侧钉住：8 搜索 + 5 聚合/文档，缺任一注册模块都会在此暴露。"""
    names = _names()
    assert len(_SEARCH_TOOLS & names) == 8
    assert len(_AGG_DOC_TOOLS & names) == 5


def test_capabilities_and_schemas_share_one_source():
    """``kernel.CAPABILITIES()`` 与 ``kernel.build_tool_schemas()`` 同源（两种取法一致）。"""
    import fieldwork_agent.kernel as k

    assert set(k.CAPABILITIES()) >= _SEARCH_TOOLS | _AGG_DOC_TOOLS
    assert len(k.CAPABILITIES()) == len(k.build_tool_schemas())


# ---------------------------------------------------------------- registry handler 包络


def test_compose_proposal_registered_fn_returns_envelope():
    """注册 fn 返回 Envelope（spec §2.4）：registry 消费者不再拿到裸 ProposalDoc。"""
    meta = capabilities()["compose_proposal"]
    assert meta.fn is proposal_handler
    env = meta.fn(**_FULL_ARGS)
    assert isinstance(env, Envelope) and env.ok is True
    assert isinstance(env.data, ProposalDoc) and len(env.data.sections) == 8


def test_compose_proposal_handler_folds_missing_channel():
    """缺渠道 ValueError 折进 ok=False 包络，code+message 齐备（包络全局约束）。"""
    env = proposal_handler(ctx={}, sites=[], itinerary=None, informants=[], knowledge=[])
    assert env.ok is False
    assert env.error is not None and env.error.code == "missing_channel"
    assert "缺少渠道" in env.error.message and env.error.hint


def test_compose_proposal_handler_folds_bad_ctx_shape():
    """ctx 形状不合 ResearchContext → ValidationError 折成 invalid_arguments（先于 ValueError）。"""
    env = proposal_handler(ctx={"topic": 1}, sites=[{"name": "x"}], itinerary={"legs": []},
                           informants=[{"code": "P1"}], knowledge=[])
    assert env.ok is False and env.error is not None
    assert env.error.code == "invalid_arguments" and env.error.message


def test_compose_interview_guide_registered_fn_returns_envelope():
    """guide 注册 fn 同样返 Envelope，data 为 类型→提纲 的 dict（无 llm 走模板降级）。"""
    meta = capabilities()["compose_interview_guide"]
    assert meta.fn is guide_handler
    env = meta.fn(topic="宗族变迁", informant_types=["长老"])
    assert isinstance(env, Envelope) and env.ok is True
    assert isinstance(env.data, dict) and "长老" in env.data
    assert "知情同意" in env.data["长老"]


# ---------------------------------------------------------------- 宿主 dispatch 形状


def test_dispatch_document_capability_returns_envelope(fake_config):
    """宿主 dispatch 文档能力得 Envelope；已包络结果直用（幂等，不二次包裹）。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)  # llm=None → 文档能力模板降级
    env = agent.client_kernel_dispatch("compose_proposal", dict(_FULL_ARGS))
    assert isinstance(env, Envelope) and env.ok is True
    assert isinstance(env.data, ProposalDoc) and len(env.data.sections) == 8


def test_dispatch_interview_guide_returns_envelope(fake_config):
    """guide 经 dispatch 亦是包络（dict 折进 data），loop 侧序列化形状与其他能力统一。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    env = agent.client_kernel_dispatch(
        "compose_interview_guide", {"topic": "宗族变迁", "informant_types": ["集市商户"]})
    assert isinstance(env, Envelope) and env.ok is True
    assert isinstance(env.data, dict) and "集市商户" in env.data


def test_dispatch_missing_channel_becomes_missing_channel_code(fake_config):
    """渠道不齐不再混进 internal_error：handler 折出的 missing_channel 包络原样透传。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    env = agent.client_kernel_dispatch("compose_proposal", {"ctx": {}, "sites": []})
    assert env.ok is False and env.error is not None
    assert env.error.code == "missing_channel" and "缺少渠道" in env.error.message
