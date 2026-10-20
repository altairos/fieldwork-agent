# tests/test_confirm_hash.py
"""五路依赖指纹（审计二 R09）：稳定性、数值规范化、五路敏感性与错误码。"""
import hashlib
import json

import pytest
from fieldwork_agent.kernel.repository.confirm_hash import (
    ProposalConfirmError,
    _canonical,
    compute_confirm_hash,
    compute_doc_hash,
)


def test_r09_hash_stable_across_ctx_key_order():
    """键序无关：同内容不同插入序的 ctx 指纹一致（sha1 hex 40 位）。"""
    a = compute_confirm_hash({"budget": "2万", "topic": "大理"}, [], [], [])
    b = compute_confirm_hash({"topic": "大理", "budget": "2万"}, [], [], [])
    assert a == b and len(a) == 40


def test_r09_int_float_equal_values_same_hash():
    """数值规范化：100 与 100.0 指纹必须一致（消除浮点表示噪声）。"""
    a = compute_confirm_hash({}, [{"lat": 100}], [], [])
    b = compute_confirm_hash({}, [{"lat": 100.0}], [], [])
    assert a == b


def test_r09_any_road_change_flips_hash():
    """五路依赖逐一变化都翻转指纹：ctx/田野点/报道人/知识库/行程快照 id。"""
    base = compute_confirm_hash({"budget": "2万"}, [{"name": "喜洲"}],
                                [{"name": "老杨"}], [{"id": "k1"}], "cache_1")
    changes = [
        ({"budget": "3万"}, [{"name": "喜洲"}], [{"name": "老杨"}],
         [{"id": "k1"}], "cache_1"),
        ({"budget": "2万"}, [{"name": "周城"}], [{"name": "老杨"}],
         [{"id": "k1"}], "cache_1"),
        ({"budget": "2万"}, [{"name": "喜洲"}], [{"name": "老张"}],
         [{"id": "k1"}], "cache_1"),
        ({"budget": "2万"}, [{"name": "喜洲"}], [{"name": "老杨"}],
         [{"id": "k2"}], "cache_1"),
        ({"budget": "2万"}, [{"name": "喜洲"}], [{"name": "老杨"}],
         [{"id": "k1"}], "cache_2"),
    ]
    for changed in changes:
        assert base != compute_confirm_hash(*changed)


def test_t02_soft_ctx_fields_do_not_flip_v3_hash():
    """T02：v3 事实指纹忽略 purpose/title 等软字段；预算等事实字段仍敏感。"""
    base_ctx = {"topic": "宗族", "budget": "2万", "purpose": "global", "title": ""}
    base = compute_doc_hash(base_ctx, [], [], [], None)
    soft = {**base_ctx, "purpose": "course", "title": "自动命名"}
    assert compute_doc_hash(soft, [], [], [], None) == base
    assert compute_doc_hash({**base_ctx, "budget": "9万"}, [], [], [], None) != base


def test_t02_v2_payload_exactly_matches_old_scheme():
    """T02：include_soft_ctx=True 必须逐字复现 v2 payload——存量文档确认兼容。"""
    ctx = {"topic": "宗族", "budget": "2万", "purpose": "global",
           "title": "旧项目", "research_notes": [{"id": "n1"}],
           "interview_state": {"current_focus": "材料"}}
    payload = {
        "schema": "doc_inputs_v2",
        "ctx": _canonical({k: v for k, v in ctx.items()
                            if k not in {"research_notes", "interview_state"}}),
        "sites": [],
        "informants": [],
        "knowledge": [],
        "itinerary": None,
    }
    expected = hashlib.sha1(json.dumps(
        payload, sort_keys=True, ensure_ascii=False,
        default=str).encode("utf-8")).hexdigest()
    assert compute_doc_hash(ctx, [], [], [], None,
                            include_soft_ctx=True) == expected


def test_r09_proposal_confirm_error_carries_code():
    with pytest.raises(ProposalConfirmError) as ei:
        raise ProposalConfirmError("文档不存在: prop_x", "not_found")
    assert ei.value.code == "not_found"
    assert str(ei.value) == "文档不存在: prop_x"
