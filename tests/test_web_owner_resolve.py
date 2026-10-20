# tests/test_web_owner_resolve.py
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fieldwork_agent.web_host.api.owner import owner_from_request


class FakeUserStore:
    def __init__(self, m):
        self.m = m

    def resolve_session(self, raw):
        return self.m.get(raw)


def _req(token, store):
    headers = {"X-Owner-Token": token} if token is not None else {}
    return SimpleNamespace(headers=headers,
                           app=SimpleNamespace(state=SimpleNamespace(user_store=store)))


def test_missing_token_none():
    assert owner_from_request(_req(None, None)) is None


def test_anon_token():
    assert owner_from_request(_req("abc123-anon-token", None)) == "anon:abc123-anon-token"


def test_valid_session():
    tok = "sess_test_session_0001"
    assert owner_from_request(_req(tok, FakeUserStore({tok: "u_1"}))) == "user:u_1"


def test_expired_session_401():
    with pytest.raises(HTTPException) as ei:
        owner_from_request(_req("sess_gone_session_0001", FakeUserStore({})))
    assert ei.value.status_code == 401 and ei.value.detail["code"] == "session_expired"


@pytest.mark.parametrize("raw", ["bad token", "sess_ a", "sess_" + "a" * 200])
def test_malformed_token_400(raw):
    """形状 400 先于会话查询：sess_ 前缀但形状非法 → 不触 store。

    store=None 即顺序证明——若实现先查会话，sess_ 前缀遇 None store
    会抛 SessionExpired 折 401，断言 400 随之失败。
    """
    with pytest.raises(HTTPException) as ei:
        owner_from_request(_req(raw, None))
    assert ei.value.status_code == 400
