# tests/test_owner_resolve.py
import pytest
from fieldwork_agent.kernel.identity.owner_resolve import SessionExpired, resolve_owner


class FakeUserStore:
    def __init__(self, mapping):
        self.m = mapping

    def resolve_session(self, raw):
        return self.m.get(raw)


def test_anon_token_no_db_hit():
    assert resolve_owner("abc123def", None) == "anon:abc123def"   # 匿名不查库


def test_valid_session_maps_to_user():
    assert resolve_owner("sess_x", FakeUserStore({"sess_x": "u_1"})) == "user:u_1"


def test_expired_session_raises():
    with pytest.raises(SessionExpired):
        resolve_owner("sess_gone", FakeUserStore({}))


def test_session_without_store_raises():
    with pytest.raises(SessionExpired):
        resolve_owner("sess_x", None)
