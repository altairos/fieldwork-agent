# tests/test_web_owner.py
"""归属令牌解析与归属守卫（spec §归属令牌 / §端点矩阵）。

owner_from_request 五分支：头缺失 / 空白串 / 合法形状 / 非法形状 / 超长。
checked_owned_project 六分支：归属相符 / 不符 / 项目无主 / 无令牌 /
项目不存在 / 非法 project_id（400 先于归属判定）。
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fieldwork_agent.web_host.api.guard import checked_owned_project
from fieldwork_agent.web_host.api.owner import owner_from_request
from starlette.requests import Request


def make_request(headers: dict[str, str]) -> Request:
    """最小 Request：解析器除 request.headers 外还急切访问
    request.app.state.user_store（会话 resolver），scope 需携带最小 app 替身。"""
    # ASGI 规范：header 名由服务器小写下发，构造时对齐真实流量
    # （starlette Headers.get 以小写键查找，scope 混排大小写将查不到）。
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(scope={"type": "http", "headers": raw,
                          "app": SimpleNamespace(state=SimpleNamespace(user_store=None))})


class FakeRepo:
    """get_state：'ghost' 抛 KeyError（项目不存在），否则返回 {"owner": ...}。

    不定义 root 属性 → checked_project_id 的 containment 检查自动跳过
    （kernel guard 取 root=getattr(repo, "root", None)）。
    """

    def __init__(self, owner: str | None):
        self._owner = owner

    def get_state(self, project_id: str) -> dict:
        if project_id == "ghost":
            raise KeyError(project_id)
        return {"owner": self._owner}


# ---------------------------------------------------------- owner_from_request

def test_owner_token_missing_returns_none():
    assert owner_from_request(make_request({})) is None


def test_owner_token_blank_returns_none():
    """空串与纯空白视为未携带（strip 后判空）。"""
    assert owner_from_request(make_request({"X-Owner-Token": "  "})) is None


def test_owner_token_valid_normalizes_to_anon():
    req = make_request({"X-Owner-Token": "abc-XYZ_123"})
    assert owner_from_request(req) == "anon:abc-XYZ_123"


@pytest.mark.parametrize("raw", ["非法字符", "a:b", "a b", "a/b"])
def test_owner_token_invalid_shape_raises_400(raw):
    with pytest.raises(HTTPException) as ei:
        owner_from_request(make_request({"X-Owner-Token": raw}))
    assert ei.value.status_code == 400
    assert ei.value.detail == {"code": "invalid_owner_token",
                               "message": "非法 X-Owner-Token 头"}


def test_owner_token_over_128_chars_raises_400():
    with pytest.raises(HTTPException) as ei:
        owner_from_request(make_request({"X-Owner-Token": "a" * 129}))
    assert ei.value.status_code == 400


# ------------------------------------------------------ checked_owned_project

def test_owned_guard_passes_when_owner_matches():
    state = checked_owned_project("p1", FakeRepo("anon:alice"), "anon:alice")
    assert state == {"owner": "anon:alice"}  # 返回快照供端点组装


def test_owned_guard_404_when_owner_mismatch():
    with pytest.raises(HTTPException) as ei:
        checked_owned_project("p1", FakeRepo("anon:alice"), "anon:bob")
    assert ei.value.status_code == 404
    assert ei.value.detail == "项目不存在: p1"


def test_owned_guard_404_when_project_ownerless():
    """历史无主项目对任何令牌不可见（spec §端点矩阵）。"""
    with pytest.raises(HTTPException) as ei:
        checked_owned_project("p1", FakeRepo(None), "anon:alice")
    assert ei.value.status_code == 404


def test_owned_guard_404_when_request_has_no_token():
    with pytest.raises(HTTPException) as ei:
        checked_owned_project("p1", FakeRepo("anon:alice"), None)
    assert ei.value.status_code == 404


def test_owned_guard_404_when_project_missing():
    with pytest.raises(HTTPException) as ei:
        checked_owned_project("ghost", FakeRepo("anon:alice"), "anon:alice")
    assert ei.value.status_code == 404
    assert ei.value.detail == "项目不存在: ghost"


def test_owned_guard_invalid_pid_raises_400_before_ownership():
    """形状错误 400 先于归属判定（对齐既有守卫顺序语义）。"""
    with pytest.raises(HTTPException) as ei:
        checked_owned_project("C:evil", FakeRepo("anon:alice"), "anon:alice")
    assert ei.value.status_code == 400
    assert ei.value.detail["code"] == "invalid_project_id"
