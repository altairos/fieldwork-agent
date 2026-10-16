# tests/test_confirm_hash.py
"""五路依赖指纹（审计二 R09）：稳定性、数值规范化、五路敏感性与错误码。"""
import pytest
from fieldwork_agent.kernel.repository.confirm_hash import (
    ProposalConfirmError,
    compute_confirm_hash,
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


def test_r09_proposal_confirm_error_carries_code():
    with pytest.raises(ProposalConfirmError) as ei:
        raise ProposalConfirmError("文档不存在: prop_x", "not_found")
    assert ei.value.code == "not_found"
    assert str(ei.value) == "文档不存在: prop_x"
