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
    assert owner_from_request(_req("abc123", None)) == "anon:abc123"


def test_valid_session():
    assert owner_from_request(_req("sess_x", FakeUserStore({"sess_x": "u_1"}))) == "user:u_1"


def test_expired_session_401():
    with pytest.raises(HTTPException) as ei:
        owner_from_request(_req("sess_gone", FakeUserStore({})))
    assert ei.value.status_code == 401 and ei.value.detail["code"] == "session_expired"


def test_malformed_token_400():
    with pytest.raises(HTTPException) as ei:
        owner_from_request(_req("bad token", None))
    assert ei.value.status_code == 400
