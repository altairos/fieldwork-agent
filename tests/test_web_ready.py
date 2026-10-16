# tests/test_web_ready.py
"""审计 F15：/ready 就绪端点（blocking = flyai CLI + 项目目录可写）。"""

import pytest
from httpx import ASGITransport, AsyncClient

from fieldwork_agent.web_host.app import create_app


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    # 生产路径中 Config._ensure_dirs() 会先建 projects 目录，自定义 repo 绕过了
    # 该步（JsonRepository 构造不 mkdir），此处显式补齐以还原真实前提。
    projects = tmp_path / "projects"
    projects.mkdir()
    repo = JsonRepository(projects_dir=projects)
    _app = create_app(config=None, repo=repo)
    init_deps(_app, config=None, repo=repo)
    return _app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_ready_ok(client, monkeypatch):
    # CI 机器通常没装 flyai CLI：monkeypatch 成假路径保证确定性
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/flyai" if name == "flyai" else None)
    r = await client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["flyai_cli"] is True
    assert body["checks"]["projects_dir_writable"] is True


async def test_ready_missing_flyai_cli_503(client, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = await client.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["flyai_cli"] is False


async def test_ready_unwritable_projects_dir_503(client, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/flyai")

    def denied(self, *a, **k):
        raise OSError("read-only")

    monkeypatch.setattr("pathlib.Path.touch", denied)
    r = await client.get("/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["projects_dir_writable"] is False
