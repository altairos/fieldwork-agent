# tests/test_web_ready.py
"""审计 F15：/ready 就绪端点（blocking = flyai CLI + 项目目录可写）。"""

import pytest
from fieldwork_agent.web_host.app import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    # 生产路径中 create_app 自建仓储时会调 Config.ensure_dirs() 先建 projects
    # 目录，自定义 repo 绕过了该步（JsonRepository 构造不 mkdir），此处显式
    # 补齐以还原真实前提。
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


async def test_ready_amap_js_key_soft_check(app, monkeypatch):
    """N6：amap_js_key_configured 软检查——缺失/在位均不进 blocking，status 恒 ready。"""
    from types import SimpleNamespace

    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/flyai" if name == "flyai" else None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        app.state.config = SimpleNamespace(flyai_api_key="", amap_js_key="")
        r = await c.get("/ready")
        assert r.status_code == 200                     # 缺 JS Key 不阻塞（地图面板降级卡片）
        assert r.json()["checks"]["amap_js_key_configured"] is False

        app.state.config = SimpleNamespace(flyai_api_key="", amap_js_key="jsk")
        r = await c.get("/ready")
        assert r.status_code == 200
        assert r.json()["checks"]["amap_js_key_configured"] is True


async def test_ready_probe_filename_unique_per_call(client, tmp_path, monkeypatch):
    """终审#2：并发 /ready 的 touch→unlink 互踩 → 假 503。

    探针文件名必须每次调用唯一（pid + monotonic_ns）；确定性竞态难以复现，
    退而钉住不变量：两次 /ready 触碰的文件名互不相同，且无探针残留。
    """
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/flyai" if name == "flyai" else None)

    import pathlib

    names: list[str] = []
    real_touch = pathlib.Path.touch

    def spy(self, *a, **k):
        names.append(self.name)
        return real_touch(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "touch", spy)

    r1 = await client.get("/ready")
    r2 = await client.get("/ready")
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(names) == 2
    assert names[0] != names[1]
    assert all(n.startswith(".ready_probe.") for n in names)
    # touch→unlink 后不留探针残留（目录里只剩 repo 自身文件结构）
    assert list((tmp_path / "projects").glob(".ready_probe*")) == []
