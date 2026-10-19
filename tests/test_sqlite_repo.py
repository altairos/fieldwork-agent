"""SqliteRepository 实现细节单测（契约平价由 repository_contract 双跑把关，这里测 sqlite 专属）。"""
import json
import sqlite3
import threading
from pathlib import Path
from unittest import mock

import fieldwork_agent.kernel.repository.sqlite_repo as sqlite_repo_mod
import pytest
from fieldwork_agent.kernel.repository.confirm_hash import (
    ProposalConfirmError,
    compute_confirm_hash,
)
from fieldwork_agent.kernel.repository.sqlite_repo import SCHEMA_VERSION, SqliteRepository


def make_sqlite_repo(tmp_path: Path) -> SqliteRepository:
    return SqliteRepository(db_path=tmp_path / "fieldwork.db")


def test_schema_created_with_user_version(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with sqlite3.connect(repo.db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert version == SCHEMA_VERSION
    for t in ("projects", "research_context", "messages", "field_sites",
              "informants", "search_cache", "knowledge", "proposals",
              "confirmations", "schema_meta"):
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


def test_messages_full_history_no_loss(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    for i in range(60):
        repo.append_message("p1", "user", f"m{i}")
    assert len(repo.get_messages("p1", limit=100)) == 60


def test_messages_fields_and_order(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.append_message("p1", "user", "提问", meta={"tool": "search_ai"})
    repo.append_message("p1", "assistant", "回答")
    msgs = repo.get_messages("p1", limit=2)
    assert [m["content"] for m in msgs] == ["提问", "回答"]
    assert msgs[0]["role"] == "user"
    assert msgs[0]["metadata"] == {"tool": "search_ai"}
    assert msgs[1]["metadata"] == {}
    assert all(m["ts"] for m in msgs)


def test_messages_limit_returns_tail(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    for i in range(10):
        repo.append_message("p1", "user", f"m{i}")
    assert [m["content"] for m in repo.get_messages("p1", limit=3)] == ["m7", "m8", "m9"]


def test_dialogue_context_window_capped_50(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    for i in range(60):
        repo.append_message("p1", "user", f"m{i}")
    assert len(repo.get_state("p1")["dialogue_context"]) == 50   # 窗口副本 ≤50
    assert len(repo.get_messages("p1", limit=0)) == 60           # 全量真源不丢


def test_site_informant_roundtrip(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.save_field_site("p1", {"name": "喜洲", "confirmed": True})
    repo.save_informant("p1", {"code": "P1", "type": "长老"})
    assert repo.list_field_sites("p1")[0]["confirmed"] is True
    assert repo.list_informants("p1")[0]["type"] == "长老"


def test_record_without_id_gets_one(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    sid = repo.save_field_site("p1", {"name": "大理"})
    iid = repo.save_informant("p1", {"code": "P2"})
    assert sid and iid
    assert repo.list_field_sites("p1")[0]["id"] == sid
    assert repo.list_informants("p1")[0]["id"] == iid


def test_same_id_updates_in_place(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    sid = repo.save_field_site("p1", {"name": "喜洲", "confirmed": False})
    repo.save_field_site("p1", {"id": sid, "name": "喜洲", "confirmed": True})
    sites = repo.list_field_sites("p1")
    assert len(sites) == 1 and sites[0]["confirmed"] is True


def test_arbitrary_fields_roundtrip(tmp_path):
    """schemaless：任意额外字段必须原样 round-trip（迁移全等的前提）。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.save_field_site("p1", {"name": "X", "custom": {"nested": [1, 2]}, "confirmed": False})
    got = repo.list_field_sites("p1")[0]
    assert got["custom"] == {"nested": [1, 2]}
    assert "profile" not in got   # 不得凭空填充未提供的键（与 Json 平价）


def test_save_order_preserved(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    for n in ("a", "b", "c"):
        repo.save_field_site("p1", {"name": n})
    assert [s["name"] for s in repo.list_field_sites("p1")] == ["a", "b", "c"]


def test_cache_roundtrip(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    cid = repo.cache_search("p1", "大理住宿", "search_hotel", {"items": [1, 2]})
    full = repo.get_cached_search("p1", cid)
    assert full["query"] == "大理住宿" and full["tool"] == "search_hotel"
    assert full["result"] == {"items": [1, 2]}
    assert [c["id"] for c in repo.list_cached_searches("p1")] == [cid]
    assert "result" not in repo.list_cached_searches("p1")[0]   # 索引不含 result


def test_unknown_cache_id_returns_none(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    assert repo.get_cached_search("p1", "no_such_id") is None


def test_cache_truncated_64kb(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    cid = repo.cache_search("p1", "q", "search_ai", {"blob": "x" * 100_000})
    stored = repo.get_cached_search("p1", cid)["result"]
    assert stored["truncated"] is True
    assert len(stored["preview"].encode("utf-8")) <= 64 * 1024


def test_cache_index_legacy_row_unknown(tmp_path):
    """R12：迁移前的旧行新列为 NULL → 索引 empty/truncated/total 为 None（前端 unknown），
    既有 4 字段不受影响。"""
    db = tmp_path / "fieldwork.db"
    with sqlite3.connect(db) as conn:   # 对照 _V1_MIN：先建 v3 旧 schema 库再重开触发迁移
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, phase TEXT NOT NULL,
                phase_history TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE search_cache (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, query TEXT NOT NULL,
                tool TEXT NOT NULL, result TEXT NOT NULL, cached_at TEXT NOT NULL);
            PRAGMA user_version=3;
            """)
        conn.execute(
            "INSERT INTO projects(project_id,created_at,updated_at,phase,phase_history)"
            " VALUES('p1','2026-01-01','2026-01-02','research_positioning','[]')")
        conn.execute(
            "INSERT INTO search_cache(id,project_id,query,tool,result,cached_at)"
            " VALUES(?,?,?,?,?,?)",
            ("srch_20260101_000000_legacy0", "p1", "q", "search_ai",
             '{"items":[],"total":0}', "2026-01-01T00:00:00"))
    repo = SqliteRepository(db_path=db)   # 重开 → v3→v4 迁移补列（旧行新列 NULL）
    row = repo.list_cached_searches("p1")[0]
    assert row["empty"] is None and row["truncated"] is None and row["total"] is None
    assert row["query_key"] == "" and row["parent_call_id"] == "" and row["turn_id"] == ""
    assert (row["id"], row["query"], row["tool"], row["cached_at"]) == (
        "srch_20260101_000000_legacy0", "q", "search_ai", "2026-01-01T00:00:00")


def test_knowledge_roundtrip_and_search(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    kid = repo.add_knowledge("p1", "白族三月街", "集市访谈材料", tags=["田野", "集市"])
    entries = repo.list_knowledge("p1")
    assert [e["id"] for e in entries] == [kid]
    assert entries[0]["source"] == "user" and entries[0]["category"] == "general"
    assert entries[0]["tags"] == ["田野", "集市"]
    assert [e["id"] for e in repo.search_knowledge("p1", "三月街")] == [kid]   # 子串
    assert repo.search_knowledge("p1", "集市 访谈") == entries                # 2字token AND
    assert repo.search_knowledge("p1", "不存在") == []


def test_knowledge_search_case_insensitive(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.add_knowledge("p1", "Field Notes", "Ethnography")
    assert len(repo.search_knowledge("p1", "field")) == 1     # 大小写不敏感
    assert len(repo.search_knowledge("p1", "ETHNO")) == 1


def test_delete_knowledge_idempotent(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    kid = repo.add_knowledge("p1", "提纲", "问题1", category="methods")
    repo.delete_knowledge("p1", kid)
    repo.delete_knowledge("p1", kid)   # 再删不报错
    assert repo.list_knowledge("p1") == []


def test_proposal_versions_accumulate(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.save_proposal("p1", "v1", version="draft")
    repo.save_proposal("p1", "v2", version="draft")
    assert len(repo.list_proposals("p1")) == 2   # 不覆盖
    assert repo.get_latest_proposal("p1")["content"] == "v2"


def test_proposal_record_fields(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    pid = repo.save_proposal("p1", "# 研究方案", kind="proposal", version="final")
    latest = repo.get_latest_proposal("p1")
    assert latest["id"] == pid
    assert latest["kind"] == "proposal" and latest["version"] == "final"
    assert latest["filename"].endswith(".md")
    assert latest["saved_at"]
    assert latest["content"] == "# 研究方案"


def test_no_proposal_returns_none(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    assert repo.get_latest_proposal("p1") is None
    assert repo.list_proposals("p1") == []


def test_get_state_proposals_exclude_content(tmp_path):
    """get_state 的 proposals 是登记记录（不含 content），对齐 Json。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.save_proposal("p1", "正文", version="draft")
    st = repo.get_state("p1")
    assert len(st["proposals"]) == 1
    assert "content" not in st["proposals"][0]
    assert st["proposals"][0]["filename"].endswith(".md")


_V1_MIN = """
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    phase TEXT NOT NULL, phase_history TEXT NOT NULL DEFAULT '[]');
CREATE TABLE proposals (
    id TEXT NOT NULL, project_id TEXT NOT NULL, kind TEXT NOT NULL, version TEXT NOT NULL,
    filename TEXT NOT NULL, content TEXT NOT NULL, saved_at TEXT NOT NULL, seq INTEGER NOT NULL,
    PRIMARY KEY (project_id, id));
PRAGMA user_version=1;
"""


def test_schema_version_is_current(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "dep_hash" in {r[1] for r in conn.execute("PRAGMA table_info(proposals)")}
        assert "proposal_confirmed" in {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
        assert "owner" in {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "confirmations" in tables


def test_v1_db_upgrades_to_current_in_place(tmp_path):
    """存量 v1 库：增量补列补表、版本推进、既有行原样保留（绝不 DROP 重建）。

    v1 库重开直达当前版（v2 的 proposal_confirmed/dep_hash、v3 的 owner、
    v4 的 search_cache 六列与复合主键一并补齐）。"""
    db = tmp_path / "fieldwork.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(_V1_MIN)
        conn.execute(
            "INSERT INTO projects(project_id,created_at,updated_at,phase,phase_history)"
            " VALUES('p1','2026-01-01','2026-01-02','field_site_profiles','[]')")
        conn.execute(
            "INSERT INTO proposals(id,project_id,kind,version,filename,content,saved_at,seq)"
            " VALUES('prop_1','p1','proposal','final','a.md','正文','2026-01-02',1)")
    repo = SqliteRepository(db_path=db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        row = conn.execute(
            "SELECT p.proposal_confirmed, q.dep_hash FROM projects p"
            " JOIN proposals q ON q.project_id = p.project_id"
            " WHERE p.project_id='p1'").fetchone()
    assert row[0] == 0 and row[1] == ""      # 默认值补齐，不改语义
    assert repo.get_state("p1")["owner"] is None   # v1 旧项目无主
    assert repo.get_phase("p1") == "field_site_profiles"
    assert repo.list_proposals("p1")[0]["content"] == "正文"


def test_half_migrated_v1_db_reopens(tmp_path):
    """半迁移库恢复（终审 T2-M1）：两列已加、user_version 仍=1（迁移中途被杀的
    等价终态）——打开必须成功并补齐版本，而非 duplicate column 锁死。"""
    db = tmp_path / "fieldwork.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(_V1_MIN)
        conn.execute(
            "INSERT INTO projects(project_id,created_at,updated_at,phase,phase_history)"
            " VALUES('p1','2026-01-01','2026-01-02','field_site_profiles','[]')")
        conn.execute("ALTER TABLE projects ADD COLUMN proposal_confirmed"
                     " INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE proposals ADD COLUMN dep_hash TEXT NOT NULL DEFAULT ''")
    repo = SqliteRepository(db_path=db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert repo.get_phase("p1") == "field_site_profiles"


def test_v1_to_current_migration_atomic_on_failure(tmp_path, monkeypatch):
    """增量迁移原子性（终审 T2-M1）：事务尾段失败 → 补列与版本推进一并不落盘。"""
    db = tmp_path / "fieldwork.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(_V1_MIN)
        conn.execute(
            "INSERT INTO projects(project_id,created_at,updated_at,phase,phase_history)"
            " VALUES('p1','2026-01-01','2026-01-02','field_site_profiles','[]')")
    from fieldwork_agent.kernel.repository import sqlite_repo as sr
    poison = ("projects", "no_such_column", "SELECT boom")   # 同型三元组、语句必败
    monkeypatch.setattr(sr, "_MIGRATE_COLUMNS",
                        sr._MIGRATE_COLUMNS + (poison,))
    with pytest.raises(sqlite3.OperationalError):
        SqliteRepository(db_path=db)
    with sqlite3.connect(db) as conn:
        assert "dep_hash" not in {r[1] for r in conn.execute("PRAGMA table_info(proposals)")}
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    monkeypatch.undo()
    assert SqliteRepository(db_path=db).get_phase("p1") == "field_site_profiles"


def test_future_schema_version_rejected(tmp_path):
    """高版本库不得被低版本代码降级打开（R02 版本纪律）。"""
    db = tmp_path / "fieldwork.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t(x)")
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="schema 版本"):
        SqliteRepository(db_path=db)


def test_save_proposal_records_dep_hash(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.update_research_context("p1", {"topic": "宗族"})
    rid = repo.save_proposal("p1", "# 方案", version="final")
    expected = compute_confirm_hash(
        repo.get_research_context("p1"), repo.list_field_sites("p1"),
        repo.list_informants("p1"), repo.list_knowledge("p1"), "")
    with sqlite3.connect(repo.db_path) as conn:
        stored = conn.execute(
            "SELECT dep_hash FROM proposals WHERE id=?", (rid,)).fetchone()[0]
    assert stored == expected and len(stored) == 40
    assert repo.get_state("p1")["proposals"][0]["dep_hash"] == stored
    assert repo.list_proposals("p1")[0]["dep_hash"] == stored


def test_get_state_omits_empty_dep_hash(tmp_path):
    """legacy 迁移行（dep_hash 空串）：state 侧省略该键，list_proposals 恒补空键。

    json 侧两种读出形状不同：``get_state`` 回原始登记记录（R09 前的记录本就无
    ``dep_hash`` 键），而 ``list_proposals`` 经 ``_proposal_doc`` 恒
    ``setdefault("dep_hash", "")``（迁移 ``_verify`` 首条哈希比对依赖七键平价）。
    """
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo._import_proposal("p1", {"id": "legacy_proposal_draft", "kind": "proposal",
                                 "version": "draft", "filename": "l.md",
                                 "content": "旧", "saved_at": "2026-01-01"})
    assert "dep_hash" not in repo.get_state("p1")["proposals"][0]
    assert repo.list_proposals("p1")[0]["dep_hash"] == ""


def test_get_state_confirmation_defaults(tmp_path):
    """空项目也要吐出确认两面（json `_load` 经 `_default_state` 补齐的等价物）。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    st = repo.get_state("p1")
    assert st["confirmations"] == []
    assert st["proposal_confirmed"] is False


def test_latest_itinerary_cid_takes_last_saved(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.cache_search("p1", "q1", "search_hotel", {"a": 1})
    repo.cache_search("p1", "q2", "compose_itinerary", {"b": 2})
    repo.cache_search("p1", "q3", "search_pois", {"c": 3})
    last = repo.cache_search("p1", "q4", "compose_itinerary", {"d": 4})
    with repo._connect() as conn:   # 双参形态（conn, pid）：复用内部连接工厂
        assert repo._latest_itinerary_cid(conn, "p1") == last
    assert repo._current_confirm_hash("p1") == compute_confirm_hash(
        {}, repo.list_field_sites("p1"), repo.list_informants("p1"),
        repo.list_knowledge("p1"), last)


# ------------------------------------------------------------------ R01：缓存读取收窄到项目域

def test_get_cached_search_scoped_to_project(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.create("p2")
    cid = repo.cache_search("p2", "秘密查询", "search_hotel", {"items": [1]})
    assert repo.get_cached_search("p2", cid)["query"] == "秘密查询"
    assert repo.get_cached_search("p1", cid) is None      # 跳项目 id 不可见


def test_get_cached_search_bad_cid_returns_none(tmp_path):
    """cid 先过白名单（guard.CACHE_ID_RE），不做任何文件系统/SQL 拼接。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    for bad in ("../escape", "", "a" * 65, "with space", "a\\b", None):
        assert repo.get_cached_search("p1", bad) is None


def test_root_is_db_file_parent(tmp_path):
    """宿主探针（web_host/app.py 的 /ready 可写探测、guard 的 containment）
    经 getattr(repo, "root", None) 取根；sqlite 侧给库文件父目录。"""
    repo = make_sqlite_repo(tmp_path)
    assert repo.root == tmp_path


# ------------------------------------------------------------------ R09：确认与失效

def test_confirm_field_site(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    sid = repo.save_field_site("p1", {"name": "喜洲", "confirmed": False})
    saved_at = repo.list_field_sites("p1")[0]["saved_at"]
    assert repo.confirm_field_site("p1", sid) is True
    site = repo.list_field_sites("p1")[0]
    assert site["confirmed"] is True
    assert site["saved_at"] == saved_at            # 对齐 json：确认不改条目 saved_at
    assert repo.confirm_field_site("p1", "nope") is False


def test_confirm_proposal_four_rejections(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    with pytest.raises(ProposalConfirmError) as exc:
        repo.confirm_proposal("p1", "nope")
    assert exc.value.code == "not_found"
    gid = repo.save_proposal("p1", "# 提纲", kind="interview_guide", version="final")
    with pytest.raises(ProposalConfirmError) as exc:
        repo.confirm_proposal("p1", gid)
    assert exc.value.code == "wrong_kind"
    repo._import_proposal("p1", {"id": "legacy_draft", "kind": "proposal",
                                 "version": "draft", "filename": "l.md",
                                 "content": "旧", "saved_at": "2026-01-01"})
    with pytest.raises(ProposalConfirmError) as exc:
        repo.confirm_proposal("p1", "legacy_draft")
    assert exc.value.code == "dep_hash_missing"
    rid = repo.save_proposal("p1", "# 方案", version="final")
    repo.update_research_context("p1", {"budget": "5万"})
    with pytest.raises(ProposalConfirmError) as exc:
        repo.confirm_proposal("p1", rid)
    assert exc.value.code == "dep_hash_mismatch"


def test_confirm_proposal_success_writes_confirmation(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.update_research_context("p1", {"topic": "宗族"})
    rid = repo.save_proposal("p1", "# 方案", version="final")
    rec = repo.confirm_proposal("p1", rid)
    assert rec["kind"] == "proposal" and rec["subject"] == rid
    assert rec["inputs_hash"] == "" and len(rec["dep_hash"]) == 40
    st = repo.get_state("p1")
    assert st["proposal_confirmed"] is True
    assert st["confirmations"] == [rec]          # 键集 == model_dump(mode="json")
    assert repo.is_proposal_confirmation_stale("p1") is False


def test_confirm_proposal_defaults_to_latest_registered(tmp_path):
    """doc_id 缺省取“最新一份 proposal”（P2-1），跳过 interview_guide 等其他 kind。

    旧版取登记表尾条，后存访谈指南会让空参确认误报 wrong_kind（与 json 分歧）；
    显式传入 guide 的 id 才应报 wrong_kind。
    """
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    rid = repo.save_proposal("p1", "# 方案", version="final")
    assert repo.confirm_proposal("p1")["subject"] == rid
    gid = repo.save_proposal("p1", "# 提纲", kind="interview_guide", version="final")
    assert repo.confirm_proposal("p1")["subject"] == rid   # 仍锁定 proposal，不回落 guide
    with pytest.raises(ProposalConfirmError) as exc:
        repo.confirm_proposal("p1", gid)
    assert exc.value.code == "wrong_kind"


def test_confirmation_stale_after_new_proposal(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    rid = repo.save_proposal("p1", "# 方案", version="final")
    repo.confirm_proposal("p1", rid)
    assert repo.is_proposal_confirmation_stale("p1") is False
    repo.save_proposal("p1", "# 方案 v2", version="final")
    assert repo.is_proposal_confirmation_stale("p1") is True


def test_legacy_confirmation_without_dep_hash_is_stale(tmp_path):
    """无指纹的旧确认记录：无法判定依赖，保守视失效。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo._import_confirmation("p1", {"kind": "proposal", "subject": "prop_x",
                                     "confirmed_at": "2026-01-01",
                                     "inputs_hash": "h" * 40})
    assert repo.is_proposal_confirmation_stale("p1") is True


def test_no_confirmation_is_not_stale(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.save_proposal("p1", "# 方案", version="final")
    assert repo.is_proposal_confirmation_stale("p1") is False


def test_get_proposal_by_id(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    rid = repo.save_proposal("p1", "# 方案", version="final")
    assert repo.get_proposal_by_id("p1", rid)["content"] == "# 方案"
    assert repo.get_proposal_by_id("p1", "nope") is None
    repo._import_proposal("p1", {"id": "legacy_proposal_draft", "kind": "proposal",
                                 "version": "draft", "filename": "l.md",
                                 "content": "旧", "saved_at": "2026-01-01"})
    assert repo.get_proposal_by_id("p1", "legacy_proposal_draft")["content"] == "旧"


# ------------------------------------------------------------------ R04：并发写不丢更新

def _run_concurrently(n_threads, worker):
    barrier = threading.Barrier(n_threads)

    def gate(tid):
        barrier.wait()
        worker(tid)

    threads = [threading.Thread(target=gate, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_concurrent_context_updates_no_lost_write(tmp_path):
    """读-改-写（SELECT data → merge → REPLACE）必须串行，否则浅合并丢键。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    n_threads, n_writes = 4, 15

    def worker(tid):
        for i in range(n_writes):
            repo.update_research_context("p1", {f"t{tid}_k{i}": f"v{i}"})

    _run_concurrently(n_threads, worker)
    ctx = repo.get_research_context("p1")
    assert len(ctx) == n_threads * n_writes, f"丢写：{len(ctx)} != {n_threads * n_writes}"


def test_concurrent_proposal_seq_unique(tmp_path):
    """`_next_seq`（MAX(seq)+1）并发下不得撞 seq（撞号 = 保存序错乱）。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    n_threads, n_writes = 4, 10

    def worker(tid):
        for i in range(n_writes):
            repo.save_proposal("p1", f"# 方案 {tid}-{i}", version="final")

    _run_concurrently(n_threads, worker)
    with sqlite3.connect(repo.db_path) as conn:
        seqs = [r[0] for r in conn.execute(
            "SELECT seq FROM proposals WHERE project_id='p1'")]
    assert len(seqs) == n_threads * n_writes
    assert len(set(seqs)) == n_threads * n_writes, f"seq 重复：{sorted(seqs)}"


def test_concurrent_field_site_seq_unique(tmp_path):
    """`_upsert_json_record` 的 insert 分支同走 `_next_seq`，新记录 seq 必须无重复。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    n_threads, n_writes = 4, 10

    def worker(tid):
        for i in range(n_writes):
            repo.save_field_site("p1", {"name": f"{tid}-{i}"})

    _run_concurrently(n_threads, worker)
    with sqlite3.connect(repo.db_path) as conn:
        seqs = [r[0] for r in conn.execute(
            "SELECT seq FROM field_sites WHERE project_id='p1'")]
    assert len(seqs) == n_threads * n_writes
    assert len(set(seqs)) == n_threads * n_writes
    assert len(repo.list_field_sites("p1")) == n_threads * n_writes


# ------------------------------------------------------------------ 归属隔离（v2→当前版升级）

def test_v2_database_upgrades_to_current(tmp_path):
    """v2 旧库重开自动升至当前版：projects.owner 与 search_cache 六列/
    复合主键补齐，数据无损。"""
    db = tmp_path / "fieldwork.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE projects (
                project_id    TEXT PRIMARY KEY,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                phase         TEXT NOT NULL,
                phase_history TEXT NOT NULL DEFAULT '[]',
                proposal_confirmed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE proposals (
                id         TEXT NOT NULL,
                project_id TEXT NOT NULL,
                kind       TEXT NOT NULL,
                version    TEXT NOT NULL,
                filename   TEXT NOT NULL,
                content    TEXT NOT NULL,
                saved_at   TEXT NOT NULL,
                dep_hash   TEXT NOT NULL DEFAULT '',
                seq        INTEGER NOT NULL,
                PRIMARY KEY (project_id, id)
            );
            PRAGMA user_version=2;
        """)
        conn.execute(
            "INSERT INTO projects(project_id, created_at, updated_at, phase,"
            " phase_history, proposal_confirmed)"
            " VALUES('p_old','2026-01-01','2026-01-02',"
            "'research_positioning','[]',0)")
    repo = SqliteRepository(db_path=db)   # 重开触发 v2→当前版
    assert repo.get_state("p_old")["owner"] is None   # 旧项目无主
    repo.save_proposal("p_old", "# 旧库新提案")        # 升级后读写正常
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    assert "owner" in cols


# ------------------------------------------------------------------ 缓存复合主键（N07 v3→v4）

def test_v3_db_upgrades_search_cache_to_composite_pk(tmp_path):
    """N07（v3→v4）：旧库 search_cache 全局 id 主键整表重建为 (project_id, id)，
    既有缓存行原样保留、版本号推进；升级后不同项目可合法共用同一 legacy cid。"""
    db = tmp_path / "fieldwork.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, phase TEXT NOT NULL,
                phase_history TEXT NOT NULL DEFAULT '[]',
                proposal_confirmed INTEGER NOT NULL DEFAULT 0, owner TEXT);
            CREATE TABLE search_cache (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, query TEXT NOT NULL,
                tool TEXT NOT NULL, result TEXT NOT NULL, cached_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE);
            PRAGMA user_version=3;
        """)
        conn.execute(
            "INSERT INTO projects(project_id,created_at,updated_at,phase,phase_history)"
            " VALUES('p_a','2026-01-01','2026-01-02','synthesis','[]')")
        conn.execute(
            "INSERT INTO search_cache(id,project_id,query,tool,result,cached_at)"
            " VALUES(?,?,?,?,?,?)",
            ("20260105100000_0", "p_a", "大理住宿", "search_hotel",
             json.dumps({"hotel": ["A"]}, ensure_ascii=False), "2026-01-05T10:00:00"))
    repo = SqliteRepository(db_path=db)   # 重开触发 v3→v4 整表重建
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        pk = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(search_cache)")}
    assert pk["project_id"] == 1 and pk["id"] == 2   # 复合主键 (project_id, id)
    # 既有缓存行原样保留（重建非清空）
    assert repo.get_cached_search("p_a", "20260105100000_0")["result"] == {"hotel": ["A"]}
    # 升级后另一项目可合法共用同一 cid（旧全局主键下会 REPLACE 掉 p_a 的行）
    repo.create("p_b")
    repo._import_cache("p_b", {"id": "20260105100000_0", "query": "丽江住宿",
                               "tool": "search_hotel", "result": {"hotel": ["B"]},
                               "cached_at": "2026-01-05T10:00:00"})
    assert repo.get_cached_search("p_a", "20260105100000_0")["result"] == {"hotel": ["A"]}
    assert repo.get_cached_search("p_b", "20260105100000_0")["result"] == {"hotel": ["B"]}


def test_both_v4_shapes_upgrade_to_v5(tmp_path):
    """合并版升 v5：N07 与 C5 在两条分支各自为 v4，两种形状的 v4 库重开必须互补齐。

    - HEAD 形（复合主键、无六列）：补六列，主键不动；
    - 分支形（全局 id 主键、有六列）：重建复合主键，六列数据原样保留。
    """
    base_projects = """
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, phase TEXT NOT NULL,
            phase_history TEXT NOT NULL DEFAULT '[]',
            proposal_confirmed INTEGER NOT NULL DEFAULT 0, owner TEXT);
    """
    six = {"parent_call_id", "turn_id", "query_key", "is_empty", "is_truncated", "total"}

    # HEAD 形 v4：复合主键、无六列 → 补列，主键保持
    db_a = tmp_path / "head_v4.db"
    with sqlite3.connect(db_a) as conn:
        conn.executescript(base_projects + """
            CREATE TABLE search_cache (
                id TEXT NOT NULL, project_id TEXT NOT NULL, query TEXT NOT NULL,
                tool TEXT NOT NULL, result TEXT NOT NULL, cached_at TEXT NOT NULL,
                PRIMARY KEY (project_id, id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE);
            PRAGMA user_version=4;
        """)
    SqliteRepository(db_path=db_a)   # 重开触发 v4→v5 互补齐
    with sqlite3.connect(db_a) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(search_cache)")}
        pk = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(search_cache)")}
    assert six <= cols                       # C5 六列补齐
    assert pk["project_id"] == 1 and pk["id"] == 2   # 复合主键保持（未误重建）

    # 分支形 v4：全局 id 主键、有六列（含数据）→ 重建复合主键，数据穿越保留
    db_b = tmp_path / "branch_v4.db"
    with sqlite3.connect(db_b) as conn:
        conn.executescript(base_projects + """
            CREATE TABLE search_cache (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, query TEXT NOT NULL,
                tool TEXT NOT NULL, result TEXT NOT NULL, cached_at TEXT NOT NULL,
                parent_call_id TEXT, turn_id TEXT, query_key TEXT,
                is_empty INTEGER, is_truncated INTEGER, total INTEGER,
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE);
            PRAGMA user_version=4;
        """)
        conn.execute(
            "INSERT INTO projects(project_id,created_at,updated_at,phase,phase_history)"
            " VALUES('p1','2026-01-01','2026-01-02','synthesis','[]')")
        conn.execute(
            "INSERT INTO search_cache(id,project_id,query,tool,result,cached_at,"
            " parent_call_id,turn_id,query_key,is_empty,is_truncated,total)"
            " VALUES('c1','p1','大理文献','search_literature','{}','2026-01-05T10:00:00',"
            " 'call_1','turn_1','k1',0,1,7)")
    SqliteRepository(db_path=db_b)
    with sqlite3.connect(db_b) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        pk = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(search_cache)")}
        row = conn.execute(
            "SELECT parent_call_id, total FROM search_cache WHERE project_id='p1'").fetchone()
    assert pk["project_id"] == 1 and pk["id"] == 2   # 重建为复合主键
    assert row == ("call_1", 7)                      # 六列数据穿越重建保留


def test_import_cache_same_cid_across_projects_coexist(tmp_path):
    """N07 契约：两个项目合法共用同一 legacy cid，各自快照独立、互不覆盖。

    旧全局 id 主键下 _import_cache 的 INSERT OR REPLACE 会把 A 的行搬到 B；
    复合主键 (project_id, id) 下两行并存，get_cached_search 各按项目域取回。
    """
    repo = make_sqlite_repo(tmp_path)
    repo.create("p_a")
    repo.create("p_b")
    cid = "20260105100000_0"
    repo._import_cache("p_a", {"id": cid, "query": "大理住宿", "tool": "search_hotel",
                               "result": {"hotel": ["A"]}, "cached_at": "2026-01-05T10:00:00"})
    repo._import_cache("p_b", {"id": cid, "query": "丽江住宿", "tool": "search_hotel",
                               "result": {"hotel": ["B"]}, "cached_at": "2026-01-05T10:00:00"})
    assert repo.get_cached_search("p_a", cid)["result"] == {"hotel": ["A"]}
    assert repo.get_cached_search("p_b", cid)["result"] == {"hotel": ["B"]}
    with sqlite3.connect(repo.db_path) as conn:
        rows = conn.execute(
            "SELECT project_id FROM search_cache WHERE id=? ORDER BY project_id",
            (cid,)).fetchall()
    assert [r[0] for r in rows] == ["p_a", "p_b"]   # 两行并存，非 REPLACE 移行


# ── R04：写路径并发锁（BEGIN IMMEDIATE，双进程丢更新）──────────────────

def test_update_research_context_interleaved_no_lost_update(tmp_path):
    """R04 复现审计交错：A 读旧值后挂起，让 B 完整提交一次 patch，再放行 A 写回。

    deferred 事务下 A 的读不持写锁 → A 以旧值 REPLACE，B 的键丢失；
    BEGIN IMMEDIATE 后 A 的 read-modify-write 全程持写锁，B 排队等锁，
    两个不相交 patch 俱存（对齐审计复现的 budget/topic 场景）。
    """
    repo_a = SqliteRepository(db_path=tmp_path / "conc.db")
    repo_b = SqliteRepository(db_path=tmp_path / "conc.db")   # 独立实例=独立连接，模拟 Web/MCP 双进程
    pid = "p_conc"
    repo_a.create(pid)

    a_read_done = threading.Event()
    gate = threading.Event()
    real_loads = json.loads
    state = {"first": True}
    seen_lock = threading.Lock()

    def gated_loads(s, *args, **kwargs):
        with seen_lock:
            first = state["first"]
            state["first"] = False
        if first:   # 首次 = A 的 research_context 读，卡住制造确定交错
            a_read_done.set()
            assert gate.wait(timeout=10)
        return real_loads(s, *args, **kwargs)

    with mock.patch.object(sqlite_repo_mod.json, "loads", gated_loads):
        t_a = threading.Thread(
            target=lambda: repo_a.update_research_context(pid, {"a": 1}))
        t_a.start()
        assert a_read_done.wait(timeout=10)          # A 已读完旧值
        t_b = threading.Thread(
            target=lambda: repo_b.update_research_context(pid, {"b": 2}))
        t_b.start()
        # 旧实现：B 无锁立即完成；新实现：B 阻塞在 BEGIN IMMEDIATE 等 A 的写锁
        t_b.join(timeout=2)
        gate.set()                                    # 放行 A
        t_a.join(timeout=10)
        if t_b.is_alive():                            # 新实现：B 等 A 提交后获锁
            t_b.join(timeout=10)
        assert not t_a.is_alive() and not t_b.is_alive()

    ctx = repo_a.get_research_context(pid)
    assert ctx.get("a") == 1 and ctx.get("b") == 2


def test_write_paths_open_immediate_transactions(tmp_path):
    """R04 防回归：写路径（字段合并/阶段史/序号分配/确认校验）必须读前
    先 BEGIN IMMEDIATE——用 trace 回调钉住真实语句序列。"""
    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    with mock.patch.object(sqlite_repo_mod.sqlite3, "connect",
                           side_effect=traced_connect):
        repo = SqliteRepository(db_path=tmp_path / "trace.db")
        pid = "p_trace"
        repo.create(pid)
        repo.update_research_context(pid, {"topic": "宗族"})
        repo.save_field_site(pid, {"name": "祠堂"})
        repo.add_knowledge(pid, "背景", "内容")
        repo.save_proposal(pid, "# 方案")
        repo.confirm_proposal(pid)

    upper = [s.strip().upper() for s in statements]
    assert "BEGIN IMMEDIATE" in upper
    # 字段合并的读必须在写锁之后：SELECT data 不得早于首个 BEGIN IMMEDIATE
    first_begin = upper.index("BEGIN IMMEDIATE")
    first_ctx_read = next(i for i, s in enumerate(upper)
                          if s.startswith("SELECT DATA FROM RESEARCH_CONTEXT"))
    assert first_ctx_read > first_begin
    # 六个写方法各自锁化（create/字段合并/序号分配×2/存档/确认）
    assert upper.count("BEGIN IMMEDIATE") >= 6
