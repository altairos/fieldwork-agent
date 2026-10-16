"""Task 10：前端静态挂载——API 优先 + 静态服务 + SPA fallback + 防御式跳过。

对齐 tests/test_web_projects_api.py 的注入模式：FakeConfig（conftest）+ tmp JsonRepository，
不触真实 ~/.fieldwork_agent；TestClient 上下文触发 lifespan → init_deps(注入的 config/repo)。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Fieldwork</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "logo.svg").write_text("<svg/>", encoding="utf-8")
    return dist


@pytest.fixture
def app(fake_dist, fake_config, tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.app import create_app

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    return create_app(config=fake_config, repo=repo, web_dist=fake_dist)


def test_api_routes_take_priority(app):
    """挂载静态后 /health 与 /projects 仍走 API（不被 catch-all 遮蔽）。"""
    with TestClient(app) as client:
        # F02 后 /health 另含 auth_required 键，此处只关心走 API 而非静态
        assert client.get("/health").json()["status"] == "ok"
        # /projects 是 API（返回列表），不是 index.html
        resp = client.get("/projects")
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == []


def test_static_root_serves_index(app):
    with TestClient(app) as client:
        resp = client.get("/")
        assert "Fieldwork" in resp.text
        assert resp.headers["content-type"].startswith("text/html")


def test_spa_deep_link_fallback(app):
    """BrowserRouter 深链 /p/xxx 刷新 → 返回 index.html（非 404）。"""
    with TestClient(app) as client:
        resp = client.get("/p/some_project_id")
        assert resp.status_code == 200
        assert "Fieldwork" in resp.text


def test_asset_file_served(app):
    with TestClient(app) as client:
        resp = client.get("/assets/app.js")
        assert resp.status_code == 200
        assert "console.log" in resp.text


def test_root_static_file_served(app):
    """dist 根下真实静态文件（如 logo.svg）走 catch-all 的文件分支，而非 fallback。"""
    with TestClient(app) as client:
        resp = client.get("/logo.svg")
        assert resp.status_code == 200
        assert "<svg/>" in resp.text


def test_traversal_does_not_leak_outside_dist(app, tmp_path):
    """目录穿越防护：dist 外的文件无论如何编码都不得泄出（落回 index.html）。"""
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    with TestClient(app) as client:
        for path in ("/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt"):
            resp = client.get(path)
            assert "TOP-SECRET" not in resp.text


def test_traversal_variants_blocked_direct(app, fake_dist):
    """穿越防护（多变体）：dist **父目录**的秘密文件任何 `..` 变体都拿不到。

    上一条用例的 %2e%2e 变体会被客户端解码后原样传给 handler（覆盖 is_relative_to 分支），
    而字面 `/../secret.txt` 会被 httpx 按 RFC 3986 规范化成 `/secret.txt`（走 fallback）。
    这里把两种情况一起断言：命中防护分支或落回 index.html 都算通过——关键是 secret 绝不泄露，
    且任何变体都不得让请求 5xx（TestClient 默认 raise_server_exceptions=True，异常会直接抛出）。
    """
    (fake_dist.parent / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
    with TestClient(app) as client:
        for path in (
            "/../secret.txt",                # 客户端规范化 → /secret.txt → fallback
            "/..%5csecret.txt",              # 反斜杠变体（Windows 分隔符）→ 穿越分支
            "/%2e%2e/secret.txt",            # 编码点段 → 解码后原样进 handler → 穿越分支
            "/assets/../../secret.txt",      # 多级穿越（规范化后）→ fallback
            "/assets/%2e%2e/%2e%2e/secret.txt",  # 多级编码穿越 → 穿越分支
        ):
            resp = client.get(path)
            assert resp.status_code < 500, path
            assert "TOPSECRET" not in resp.text, path


def test_malformed_path_returns_index_not_500(app):
    """畸形路径（NUL 字节）不得 500：resolve 抛 ValueError → 落回 index.html（SPA 入口）。"""
    with TestClient(app) as client:
        for path in ("/%00", "/%00secret.txt", "/p/%00"):
            resp = client.get(path)
            assert resp.status_code == 200, path
            assert "Fieldwork" in resp.text


def test_no_dist_skips_mount(fake_config, tmp_path):
    """dist 不存在 → 不挂载，API 仍正常，catch-all 不存在（根路径 404 而非崩溃）。"""
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.app import create_app

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=fake_config, repo=repo, web_dist=tmp_path / "nonexistent")
    with TestClient(_app) as client:
        assert client.get("/health").json()["status"] == "ok"
        # 无静态挂载时根路径无 catch-all → 404（不是 500 崩溃）
        assert client.get("/").status_code == 404
