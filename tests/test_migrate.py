"""Task 10：JSON → SQLite 存量搬迁（fieldwork migrate --to-sqlite，spec §5.4 里程碑 3）。

fixture 为旧 core/state.py 时代的 legacy 形状：phase_history 1 条、legacy 无 id 田野点、
proposal_draft 单值键（无 proposals[] 键）、chat_history.jsonl 2 条、knowledge/ 与
search_cache/ 各 1 文件（缓存文件键为 source_tool）、无 informants 键（空对空断言）。
"""
import shutil
from pathlib import Path

import pytest
from fieldwork_agent.core.migrate import migrate_to_sqlite
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_project"


@pytest.fixture
def legacy_json_repo(tmp_path):
    """把 legacy_project fixture 拷进临时 projects 目录，返回指向它的 JsonRepository。

    拷贝目录名必须等于 state.json 里的 project_id（legacy_project）：json 侧以
    state["project_id"] 对外报告、以目录名定位文件，两者错位会 KeyError。
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    shutil.copytree(FIXTURE, projects / "legacy_project")
    return JsonRepository(projects_dir=projects)


def test_migrate_imports_all_seven_types(legacy_json_repo, tmp_path):
    sqlite = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    report = migrate_to_sqlite(legacy_json_repo, sqlite)
    assert report["projects"] >= 1
    # 规范化核心 state 全等（迁移的 7 类）
    for pid in [p["project_id"] for p in legacy_json_repo.list_projects()]:
        js, ss = legacy_json_repo.get_state(pid), sqlite.get_state(pid)
        assert ss["phase"] == js["phase"]
        assert ss["phase_history"] == js["phase_history"]
        assert ss["research_context"] == js["research_context"]
        assert [m["content"] for m in sqlite.get_messages(pid, 0)] == \
               [m["content"] for m in legacy_json_repo.get_messages(pid, 0)]
        assert ss["field_sites"] == js["field_sites"]
        assert ss["informants"] == js["informants"]
        # 其余三类列表形状同样全等（brief L75：断言按 fixture 实况补齐）
        assert sqlite.list_knowledge(pid) == legacy_json_repo.list_knowledge(pid)
        assert sqlite.list_proposals(pid) == legacy_json_repo.list_proposals(pid)
        assert sqlite.list_cached_searches(pid) == legacy_json_repo.list_cached_searches(pid)
    # fixture 实况：2 消息、1 田野点（legacy 无 id）、0 报道人、1 缓存、1 知识、1 提案
    assert report["messages"] == 2
    assert report["field_sites"] == 1
    assert report["informants"] == 0
    assert report["search_cache"] == 1
    assert report["knowledge"] == 1
    assert report["proposals"] == 1


def test_migrate_idempotent(legacy_json_repo, tmp_path):
    sqlite = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    migrate_to_sqlite(legacy_json_repo, sqlite)
    migrate_to_sqlite(legacy_json_repo, sqlite)   # 再迁不产生重复
    pid = legacy_json_repo.list_projects()[0]["project_id"]
    assert len(sqlite.get_messages(pid, 0)) == len(legacy_json_repo.get_messages(pid, 0))


def test_migrate_marks_schema_meta(legacy_json_repo, tmp_path):
    sqlite = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    migrate_to_sqlite(legacy_json_repo, sqlite)
    import sqlite3
    with sqlite3.connect(sqlite.db_path) as conn:
        row = conn.execute("SELECT value FROM schema_meta WHERE key='migrated_from'").fetchone()
    assert row and row[0] == "json"


def test_migrate_single_project(legacy_json_repo, tmp_path):
    sqlite = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    pid = legacy_json_repo.list_projects()[0]["project_id"]
    report = migrate_to_sqlite(legacy_json_repo, sqlite, project_id=pid)
    assert report["projects"] == 1
