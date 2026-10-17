"""SqliteRepository 实现细节单测（契约平价由 repository_contract 双跑把关，这里测 sqlite 专属）。"""
import sqlite3
import threading
from pathlib import Path

import pytest
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository
from fieldwork_agent.kernel.repository.confirm_hash import ProposalConfirmError, compute_confirm_hash


def make_sqlite_repo(tmp_path: Path) -> SqliteRepository:
    return SqliteRepository(db_path=tmp_path / "fieldwork.db")


def test_schema_created_with_user_version(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with sqlite3.connect(repo.db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert version == 2
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


def test_schema_version_is_two(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "dep_hash" in {r[1] for r in conn.execute("PRAGMA table_info(proposals)")}
        assert "proposal_confirmed" in {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "confirmations" in tables


def test_v1_db_upgrades_to_v2_in_place(tmp_path):
    """存量 v1 库：增量补列补表、版本推进、既有行原样保留（绝不 DROP 重建）。"""
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        row = conn.execute(
            "SELECT p.proposal_confirmed, q.dep_hash FROM projects p"
            " JOIN proposals q ON q.project_id = p.project_id"
            " WHERE p.project_id='p1'").fetchone()
    assert row[0] == 0 and row[1] == ""      # 默认值补齐，不改语义
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert repo.get_phase("p1") == "field_site_profiles"


def test_v1_to_v2_migration_atomic_on_failure(tmp_path, monkeypatch):
    """增量迁移原子性（终审 T2-M1）：事务尾段失败 → 补列与版本推进一并不落盘。"""
    db = tmp_path / "fieldwork.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(_V1_MIN)
        conn.execute(
            "INSERT INTO projects(project_id,created_at,updated_at,phase,phase_history)"
            " VALUES('p1','2026-01-01','2026-01-02','field_site_profiles','[]')")
    from fieldwork_agent.kernel.repository import sqlite_repo as sr
    poison = ("projects", "no_such_column", "SELECT boom")   # 同型三元组、语句必败
    monkeypatch.setattr(sr, "_MIGRATE_V1_TO_V2_COLUMNS",
                        sr._MIGRATE_V1_TO_V2_COLUMNS + (poison,))
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
    """legacy 迁移行（dep_hash 空串）不得出现该键——与 json 侧旧记录键集全等。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo._import_proposal("p1", {"id": "legacy_proposal_draft", "kind": "proposal",
                                 "version": "draft", "filename": "l.md",
                                 "content": "旧", "saved_at": "2026-01-01"})
    assert "dep_hash" not in repo.get_state("p1")["proposals"][0]
    assert "dep_hash" not in repo.list_proposals("p1")[0]


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
    assert repo._latest_itinerary_cid("p1") == last
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
    """doc_id 缺省取登记表尾条（json 侧同样不限 kind：尾条是 guide 时报 wrong_kind）。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.save_proposal("p1", "# 方案", version="final")
    assert repo.confirm_proposal("p1")["kind"] == "proposal"
    repo.save_proposal("p1", "# 提纲", kind="interview_guide", version="final")
    with pytest.raises(ProposalConfirmError) as exc:
        repo.confirm_proposal("p1")
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
