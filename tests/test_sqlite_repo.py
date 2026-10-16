"""SqliteRepository 实现细节单测（契约平价由 repository_contract 双跑把关，这里测 sqlite 专属）。"""
import sqlite3
from pathlib import Path

import pytest
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


def make_sqlite_repo(tmp_path: Path) -> SqliteRepository:
    return SqliteRepository(db_path=tmp_path / "fieldwork.db")


def test_schema_created_with_user_version(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with sqlite3.connect(repo.db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert version == 1
    for t in ("projects", "research_context", "messages", "field_sites",
              "informants", "search_cache", "knowledge", "proposals", "schema_meta"):
        assert t in tables, f"缺表 {t}"


def test_wal_mode_enabled(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_unknown_project_raises_keyerror(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with pytest.raises(KeyError):
        repo._require("nope")


def test_create_idempotent_preserves(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.set_phase("p1", "field_site_profiles")
    repo.create("p1")   # 重复 create = 打开存量，不清空
    assert repo.get_phase("p1") == "field_site_profiles"


def test_list_projects_sorted_with_summary(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p2")
    repo.create("p1")
    projects = repo.list_projects()
    assert [p["project_id"] for p in projects] == ["p1", "p2"]
    assert projects[0]["phase"] == "research_positioning"
    assert projects[0]["created_at"] and projects[0]["updated_at"]


def test_set_phase_history_and_errors(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    with pytest.raises(ValueError):
        repo.set_phase("p1", "no_such_phase")
    repo.set_phase("p1", "field_site_profiles")
    repo.set_phase("p1", "field_site_profiles")   # 同阶段不写历史
    hist = repo.get_state("p1")["phase_history"]
    assert len(hist) == 1
    assert hist[-1] == {"from": "research_positioning", "to": "field_site_profiles", "at": hist[-1]["at"]}


def test_research_context_shallow_merge(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.update_research_context("p1", {"discipline": "人类学", "topic": "宗族"})
    repo.update_research_context("p1", {"topic": "宗族组织变迁"})
    ctx = repo.get_research_context("p1")
    assert ctx == {"discipline": "人类学", "topic": "宗族组织变迁"}


def test_get_state_returns_copy(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    st = repo.get_state("p1")
    st["phase"] = "hacked"
    assert repo.get_state("p1")["phase"] == "research_positioning"


def test_get_state_shape_keys(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    st = repo.get_state("p1")
    for k in ("project_id", "created_at", "updated_at", "phase", "phase_history",
              "research_context", "dialogue_context", "field_sites", "informants", "proposals"):
        assert k in st, f"缺键 {k}"


def test_instance_recreatable_same_db(tmp_path):
    """实例可随时重建（无缓存），跨实例读到即库中真相。"""
    a = make_sqlite_repo(tmp_path)
    a.create("p1")
    b = make_sqlite_repo(tmp_path)   # 新实例指同一 db 文件
    assert b.get_phase("p1") == "research_positioning"
