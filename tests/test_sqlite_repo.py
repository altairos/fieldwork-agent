"""SqliteRepository 实现细节单测（契约平价由 repository_contract 双跑把关，这里测 sqlite 专属）。"""
import json
import sqlite3
import threading
from pathlib import Path
from unittest import mock

import fieldwork_agent.kernel.repository.sqlite_repo as sqlite_repo_mod
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


def test_get_cached_search_scoped_to_project(tmp_path):
    """R01：读取收窄项目域——p1 读不到 p2 的快照，未知项目返回 None。"""
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.create("p2")
    cid = repo.cache_search("p2", "q", "search_ai", {"x": 1})
    assert repo.get_cached_search("p2", cid)["result"] == {"x": 1}
    assert repo.get_cached_search("p1", cid) is None
    assert repo.get_cached_search("no_such_project", cid) is None


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
