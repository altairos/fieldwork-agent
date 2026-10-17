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
from fieldwork_agent.core import migrate as migrate_mod
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
    assert report["confirmations"] == 0      # fixture 无确认记录
    assert report["conflicts"] == 0 and report["backups"] == 0


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


def _legacy(tmp_path):
    """拷 fixture 为单个 legacy_project 项目，返回 (json_repo, sqlite)。"""
    projects = tmp_path / "projects"
    projects.mkdir()
    shutil.copytree(FIXTURE, projects / "legacy_project")
    return (JsonRepository(projects_dir=projects),
            SqliteRepository(db_path=tmp_path / "fieldwork.db"))


def test_migrate_conflict_rejected_without_force(tmp_path):
    """R03：同名但未标迁移的项目 = 可能的原生存量，不得静默清空。"""
    json_repo, sqlite = _legacy(tmp_path)
    sqlite.create("legacy_project")                       # 无 migrated: 标记的同名项目
    sqlite.append_message("legacy_project", "user", "原生写入的消息")
    with pytest.raises(RuntimeError, match="force"):
        migrate_to_sqlite(json_repo, sqlite)
    assert len(sqlite.get_messages("legacy_project", 0)) == 1   # 未被清空
    assert sqlite.get_messages("legacy_project", 0)[0]["content"] == "原生写入的消息"


def test_migrate_force_overwrites_with_backup(tmp_path):
    json_repo, sqlite = _legacy(tmp_path)
    sqlite.create("legacy_project")
    sqlite.append_message("legacy_project", "user", "原生写入的消息")
    report = migrate_to_sqlite(json_repo, sqlite, force=True)
    assert report["conflicts"] == 1 and report["backups"] == 1
    assert len(sqlite.get_messages("legacy_project", 0)) == 4   # 覆盖为 json 侧全量
    bak = tmp_path / "fieldwork.db.bak.1"
    assert bak.exists()
    with sqlite3.connect(bak) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_backup_rolling_three(tmp_path):
    """滚动 3 份：bak.1 最新，旧的一岁岁后移，超出 keep 丢弃。"""
    json_repo, sqlite = _legacy(tmp_path)          # json_repo 本用例不用，仅为建库副作用
    del json_repo
    migrate_mod.backup(sqlite, keep=3)
    migrate_mod.backup(sqlite, keep=3)
    migrate_mod.backup(sqlite, keep=3)
    migrate_mod.backup(sqlite, keep=3)
    for i in (1, 2, 3):
        assert (tmp_path / f"fieldwork.db.bak.{i}").exists()
    assert not (tmp_path / "fieldwork.db.bak.4").exists()


def test_backup_returns_none_when_db_absent(tmp_path):
    repo = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    repo.create("p1")
    (tmp_path / "fieldwork.db").unlink()          # 模拟首迁：库文件不存在
    assert migrate_mod.backup(repo) is None


def test_migrate_imports_confirmations(tmp_path):
    """确认面（confirmations 列表 + proposal_confirmed）全量搬迁并进 _verify。"""
    json_repo, sqlite = _legacy(tmp_path)
    pid = json_repo.list_projects()[0]["project_id"]
    rid = json_repo.save_proposal(pid, "# 方案", version="final")
    rec = json_repo.confirm_proposal(pid, rid)
    report = migrate_to_sqlite(json_repo, sqlite)
    assert report["confirmations"] == 1
    js, ss = json_repo.get_state(pid), sqlite.get_state(pid)
    assert ss["confirmations"] == js["confirmations"] == [rec]
    assert ss["proposal_confirmed"] is True


def test_verify_catches_confirmation_drift(tmp_path):
    """_verify 盖到确认面：sqlite 侧确认表少一行 → 报错终止。"""
    json_repo, sqlite = _legacy(tmp_path)
    pid = json_repo.list_projects()[0]["project_id"]
    rid = json_repo.save_proposal(pid, "# 方案", version="final")
    json_repo.confirm_proposal(pid, rid)
    with sqlite._connect() as conn:                 # 先标记已迁→跳过导入，只跑 _verify
        conn.execute("INSERT INTO projects(project_id,created_at,updated_at,phase)"
                     " VALUES(?,?,?,?)", (pid, "2026-01-01", "2026-01-02",
                                          json_repo.get_phase(pid)))
        conn.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
                     (f"migrated:{pid}", "json"))
    with pytest.raises(RuntimeError, match="confirmations"):
        migrate_to_sqlite(json_repo, sqlite)
