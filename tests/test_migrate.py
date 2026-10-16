"""Task 10：JSON → SQLite 存量搬迁（fieldwork migrate --to-sqlite，spec §5.4 里程碑 3）。

fixture 为旧 core/state.py 时代的 legacy 形状：phase_history 1 条、legacy 无 id 田野点、
proposal_draft 单值键（无 proposals[] 键）、chat_history.jsonl 2 条、knowledge/ 与
search_cache/ 各 1 文件（缓存文件键为 source_tool）、无 informants 键（空对空断言）。
dialogue_context 窗口（2 条）与 jsonl（2 条）不相交，迁移经 create() 触发 backfill
补写后全量 4 条——消息史按 4 条计。
"""
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fieldwork_agent.core.migrate import migrate_to_sqlite
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_project"


def _rewrite_project_id(project_dir: Path, new_pid: str) -> None:
    """改写 state.json 的 project_id（json.load/dump，其余键原样）。

    json 侧以目录名定位文件、以 state 内 id 对外报告，两者必须一致。
    """
    state_path = project_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["project_id"] = new_pid
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                          encoding="utf-8")


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
        assert ss["created_at"] == js["created_at"]   # 时间戳保形（M2）
        assert ss["updated_at"] == js["updated_at"]
        assert [m["content"] for m in sqlite.get_messages(pid, 0)] == \
               [m["content"] for m in legacy_json_repo.get_messages(pid, 0)]
        # 窗口（C1）：sqlite 侧现算窗口 == json 侧 backfill 后的全量消息史；
        # json state 窗口是旧版滞后副本（补写只进 jsonl、不回写 state），
        # 故断言其为 sqlite 窗口的尾部子集而非全等
        assert ss["dialogue_context"] == legacy_json_repo.get_messages(pid, 0)
        assert js["dialogue_context"] == ss["dialogue_context"][-len(js["dialogue_context"]):]
        assert ss["field_sites"] == js["field_sites"]
        assert ss["informants"] == js["informants"]
        # 其余三类列表形状同样全等（brief L75：断言按 fixture 实况补齐）
        assert sqlite.list_knowledge(pid) == legacy_json_repo.list_knowledge(pid)
        assert sqlite.list_proposals(pid) == legacy_json_repo.list_proposals(pid)
        assert sqlite.list_cached_searches(pid) == legacy_json_repo.list_cached_searches(pid)
    # fixture 实况：4 消息 = jsonl 2 + 窗口补写 2（backfill）、1 田野点（legacy 无
    # id）、0 报道人、1 缓存、1 知识、1 提案
    assert report["messages"] == 4
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


def test_migrate_two_legacy_projects_same_proposal_id(tmp_path):
    """M1：两个 legacy 项目同 id 提案（legacy_proposal_draft）必须共存。

    全局 id PRIMARY KEY 下第二个项目 INSERT OR REPLACE 会把行从 A 移到 B，
    _verify 行数不齐抛 RuntimeError 整库中止；复合主键 (project_id, id) 下
    跨项目同 id 共存。同内容无 id 田野点（_legacy_id 内容哈希同值）同理。
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    shutil.copytree(FIXTURE, projects / "legacy_a")
    shutil.copytree(FIXTURE, projects / "legacy_b")
    for pid in ("legacy_a", "legacy_b"):   # 目录名与 state 内 project_id 必须一致
        _rewrite_project_id(projects / pid, pid)
    # search_cache 的 cid 是全局唯一（已批准裁决，不在本修复范围）：错开第二份
    # 的缓存 id，避免同 cid REPLACE 移行成为本用例（proposal 冲突）的干扰源
    cache_old = projects / "legacy_b" / "search_cache" / "20260105100000_0.json"
    cached = json.loads(cache_old.read_text(encoding="utf-8"))
    cached["id"] = "20260105100001_0"
    cache_old.unlink()
    cache_old.with_name("20260105100001_0.json").write_text(
        json.dumps(cached, indent=2, ensure_ascii=False), encoding="utf-8")

    json_repo = JsonRepository(projects_dir=projects)
    sqlite = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    migrate_to_sqlite(json_repo, sqlite)   # 复合 PK 下不抛 RuntimeError（即通过）

    for pid in ("legacy_a", "legacy_b"):
        proposals = sqlite.list_proposals(pid)
        assert len(proposals) == 1
        assert proposals[0]["id"] == "legacy_proposal_draft"
        assert len(sqlite.list_field_sites(pid)) == 1   # 同 _legacy_id 田野点亦共存
    # 两行同时存在于库（跨项目同 id 共存，而非被 REPLACE 移行）
    with sqlite3.connect(sqlite.db_path) as conn:
        rows = conn.execute(
            "SELECT project_id FROM proposals WHERE id='legacy_proposal_draft'"
            " ORDER BY project_id").fetchall()
    assert [r[0] for r in rows] == ["legacy_a", "legacy_b"]
