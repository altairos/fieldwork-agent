"""L2 repository 契约套件 — 每个 ``ProjectRepository`` 实现都必须全绿（spec §5.1）。

用法：具体实现的测试文件 ``from repository_contract import TestContract, repo,
repo_factory`` 即继承全部用例；``REPO_FACTORIES`` 已双跑 Json/Sqlite，
新增实现只需追加一个工厂。

实现中立纪律：
- 全部用例仅经 Protocol 方法访问被测实现（64KB 截断经 ``get_cached_search``
  断言，不直读落盘）；
- 落盘布局、文件名、编码方式等实现专属假设一律留在 ``tests/test_json_repo.py``
  与 ``tests/test_sqlite_repo.py``。
"""

import pytest
from fieldwork_agent.kernel.guidance.phases import PHASES
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


def make_json_repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


def make_sqlite_repo(tmp_path):
    return SqliteRepository(db_path=tmp_path / "fieldwork.db")


REPO_FACTORIES = [make_json_repo, make_sqlite_repo]


@pytest.fixture(params=REPO_FACTORIES)
def repo_factory(request, tmp_path):
    """当前参数化实现的工厂（同一 tmp_path 上可反复"重开"以验证持久性）。"""
    return request.param


@pytest.fixture
def repo(repo_factory, tmp_path):
    r = repo_factory(tmp_path)
    r.create("p1")
    return r


class TestContract:
    """协议行为契约（阶段/消息/缓存/知识库/文档/项目面）。"""

    # ---------------------------------------------------------------- 阶段

    def test_phase_roundtrip(self, repo):
        assert repo.get_phase("p1") == "research_positioning"
        repo.set_phase("p1", "field_site_profiles")
        assert repo.get_state("p1")["phase_history"][-1]["to"] == "field_site_profiles"

    def test_unknown_phase_rejected(self, repo):
        with pytest.raises(ValueError):
            repo.set_phase("p1", "no_such_phase")

    def test_all_known_phases_accepted(self, repo):
        for phase in PHASES:
            repo.set_phase("p1", phase)
        assert repo.get_phase("p1") == PHASES[-1]

    def test_phase_history_records_transitions(self, repo):
        repo.set_phase("p1", "field_site_profiles")
        repo.set_phase("p1", "itinerary_planning")
        history = repo.get_state("p1")["phase_history"]
        assert [h["to"] for h in history] == ["field_site_profiles", "itinerary_planning"]
        assert history[1]["from"] == "field_site_profiles"
        assert history[1]["at"]

    def test_same_phase_writes_no_history(self, repo):
        repo.set_phase("p1", "field_site_profiles")
        repo.set_phase("p1", "field_site_profiles")
        assert len(repo.get_state("p1")["phase_history"]) == 1

    # ---------------------------------------------------------------- 消息（单轨制）

    def test_messages_full_history(self, repo):
        for i in range(60):
            repo.append_message("p1", "user", f"m{i}")
        assert len(repo.get_messages("p1", limit=100)) == 60  # 无丢失（旧双轨制的坑）

    def test_messages_tail_in_order_with_fields(self, repo):
        repo.append_message("p1", "user", "提问", meta={"tool": "search_ai"})
        repo.append_message("p1", "assistant", "回答")
        msgs = repo.get_messages("p1", limit=2)
        assert [m["content"] for m in msgs] == ["提问", "回答"]
        assert msgs[0]["role"] == "user"
        assert msgs[0]["metadata"] == {"tool": "search_ai"}
        assert msgs[1]["metadata"] == {}
        assert all(m["ts"] for m in msgs)

    def test_messages_limit_returns_tail(self, repo):
        for i in range(10):
            repo.append_message("p1", "user", f"m{i}")
        assert [m["content"] for m in repo.get_messages("p1", limit=3)] == ["m7", "m8", "m9"]

    def test_messages_empty_project(self, repo):
        assert repo.get_messages("p1") == []

    def test_pop_last_message_removes_tail(self, repo):
        repo.append_message("p1", "user", "提问")
        repo.append_message("p1", "assistant", "回答")
        popped = repo.pop_last_message("p1")
        assert popped["role"] == "assistant" and popped["content"] == "回答"
        assert [m["content"] for m in repo.get_messages("p1", limit=0)] == ["提问"]

    def test_pop_last_message_role_filter(self, repo):
        repo.append_message("p1", "user", "问")
        repo.append_message("p1", "assistant", "答")
        assert repo.pop_last_message("p1", role="user")["content"] == "问"
        assert [m["content"] for m in repo.get_messages("p1", limit=0)] == ["答"]

    def test_pop_last_message_none_when_no_match(self, repo):
        assert repo.pop_last_message("p1") is None
        repo.append_message("p1", "user", "问")
        assert repo.pop_last_message("p1", role="assistant") is None
        assert [m["content"] for m in repo.get_messages("p1", limit=0)] == ["问"]

    def test_pop_last_message_unknown_project(self, repo):
        with pytest.raises(KeyError):
            repo.pop_last_message("ghost")

    # ---------------------------------------------------------------- 研究上下文

    def test_research_context_patch_merges(self, repo):
        repo.update_research_context("p1", {"discipline": "人类学", "topic": "宗族"})
        repo.update_research_context("p1", {"topic": "宗族组织变迁"})
        ctx = repo.get_research_context("p1")
        assert ctx["discipline"] == "人类学"
        assert ctx["topic"] == "宗族组织变迁"

    # ---------------------------------------------------------------- 田野点与报道人

    def test_informants_and_sites(self, repo):
        repo.save_field_site("p1", {"name": "喜洲", "confirmed": True})
        repo.save_informant("p1", {"code": "P1", "type": "村干部"})
        assert repo.list_field_sites("p1")[0]["confirmed"] is True
        assert repo.list_informants("p1")[0]["type"] == "村干部"

    def test_record_without_id_gets_one(self, repo):
        sid = repo.save_field_site("p1", {"name": "大理"})
        iid = repo.save_informant("p1", {"code": "P2"})
        assert sid and iid
        assert repo.list_field_sites("p1")[0]["id"] == sid
        assert repo.list_informants("p1")[0]["id"] == iid

    def test_save_same_id_updates_in_place(self, repo):
        sid = repo.save_field_site("p1", {"name": "喜洲", "confirmed": False})
        repo.save_field_site("p1", {"id": sid, "name": "喜洲", "confirmed": True})
        sites = repo.list_field_sites("p1")
        assert len(sites) == 1
        assert sites[0]["confirmed"] is True

    # ---------------------------------------------------------------- 搜索缓存

    def test_cache_roundtrip(self, repo):
        cid = repo.cache_search("p1", "大理住宿", "search_hotel", {"items": [1, 2]})
        full = repo.get_cached_search("p1", cid)
        assert full["query"] == "大理住宿"
        assert full["tool"] == "search_hotel"
        assert full["result"] == {"items": [1, 2]}
        assert [c["id"] for c in repo.list_cached_searches("p1")] == [cid]
        assert repo.list_cached_searches("p1")[0]["query"] == "大理住宿"

    def test_unknown_cache_id_returns_none(self, repo):
        assert repo.get_cached_search("p1", "no_such_id") is None

    def test_r01_cache_scoped_to_own_project(self, repo, repo_factory, tmp_path):
        """审计二 R01：快照读取收窄到项目域，跨项目 id 不可见。"""
        other = repo_factory(tmp_path)
        other.create("p2")
        cid = other.cache_search("p2", "秘密查询", "search_hotel", {"items": [1]})
        assert other.get_cached_search("p2", cid)["query"] == "秘密查询"
        assert repo.get_cached_search("p1", cid) is None

    def test_cache_truncated_64kb(self, repo):
        big = {"blob": "x" * 100_000}
        cid = repo.cache_search("p1", "q", "search_ai", big)
        stored = repo.get_cached_search("p1", cid)["result"]   # 经 Protocol，不直读落盘
        assert stored["truncated"] is True
        assert len(stored["preview"].encode("utf-8")) <= 64 * 1024

    # ---------------------------------------------------------------- 知识库

    def test_knowledge_roundtrip_and_search(self, repo):
        kid = repo.add_knowledge("p1", "白族三月街", "集市访谈材料", tags=["田野", "集市"])
        entries = repo.list_knowledge("p1")
        assert [e["id"] for e in entries] == [kid]
        assert entries[0]["source"] == "user"
        assert entries[0]["category"] == "general"
        assert [e["id"] for e in repo.search_knowledge("p1", "三月街")] == [kid]
        assert repo.search_knowledge("p1", "集市 访谈") == entries
        assert repo.search_knowledge("p1", "不存在") == []

    def test_delete_knowledge(self, repo):
        kid = repo.add_knowledge("p1", "提纲", "问题1", category="methods")
        repo.delete_knowledge("p1", kid)
        assert repo.list_knowledge("p1") == []

    def test_update_knowledge(self, repo):
        """P1 右栏编辑：改 title/content 后读回新值，id/source/tags/created_at
        等落盘字段原样保留（编辑不是重建）。"""
        kid = repo.add_knowledge("p1", "白族三月街", "集市访谈材料", tags=["田野"])
        updated = repo.update_knowledge("p1", kid, title="三月街集市", content="**加粗**\n第二行")
        assert updated["id"] == kid
        assert updated["title"] == "三月街集市"
        assert updated["content"] == "**加粗**\n第二行"
        entry = repo.list_knowledge("p1")[0]
        assert entry["title"] == "三月街集市"
        assert entry["source"] == "user"
        assert entry["tags"] == ["田野"]
        assert entry["created_at"]

    def test_update_knowledge_partial(self, repo):
        """部分更新：未指定的字段不动（None = 不改，非 None 才写）。"""
        kid = repo.add_knowledge("p1", "提纲", "问题1", category="methods")
        repo.update_knowledge("p1", kid, content="问题2")
        entry = repo.list_knowledge("p1")[0]
        assert entry["title"] == "提纲"
        assert entry["content"] == "问题2"
        assert entry["category"] == "methods"

    def test_update_unknown_knowledge_raises_keyerror(self, repo):
        """编辑不存在的条目 → KeyError（与 delete 的幂等刻意不同：编辑失败
        要让用户看见，web 层转 404）。"""
        with pytest.raises(KeyError):
            repo.update_knowledge("p1", "no_such_entry", content="x")

    # ---------------------------------------------------------------- 提案文档

    def test_proposal_versions(self, repo):
        repo.save_proposal("p1", "v1", version="draft")
        repo.save_proposal("p1", "v2", version="draft")
        assert len(repo.list_proposals("p1")) == 2
        assert repo.get_latest_proposal("p1")["content"] == "v2"

    def test_proposal_record_fields(self, repo):
        pid = repo.save_proposal("p1", "# 研究方案", kind="proposal", version="final")
        latest = repo.get_latest_proposal("p1")
        assert latest["id"] == pid
        assert latest["kind"] == "proposal"
        assert latest["version"] == "final"
        assert latest["filename"].endswith(".md")
        assert latest["saved_at"]

    def test_no_proposal_returns_none(self, repo):
        assert repo.get_latest_proposal("p1") is None
        assert repo.list_proposals("p1") == []

    # ---------------------------------------------------------------- 确认与失效（第二批 F04）

    def test_confirm_and_stale_contract(self, repo):
        sid = repo.save_field_site("p1", {"name": "喜洲", "confirmed": False})
        assert repo.confirm_field_site("p1", sid) is True
        repo.update_research_context("p1", {"budget": "2万"})
        repo.save_proposal("p1", "# 方案", version="final")
        doc_id = repo.get_latest_proposal("p1")["id"]
        repo.confirm_proposal("p1", doc_id)
        assert repo.is_proposal_confirmation_stale("p1") is False
        assert repo.get_proposal_by_id("p1", doc_id)["content"] == "# 方案"
        repo.update_research_context("p1", {"budget": "5万"})
        assert repo.is_proposal_confirmation_stale("p1") is True

    def test_confirmation_stale_after_new_proposal(self, repo):
        repo.save_proposal("p1", "# 方案", version="final")
        repo.confirm_proposal("p1", repo.get_latest_proposal("p1")["id"])
        assert repo.is_proposal_confirmation_stale("p1") is False
        repo.save_proposal("p1", "# 方案 v2", version="final")
        assert repo.is_proposal_confirmation_stale("p1") is True

    def test_confirm_without_doc_id_picks_latest_proposal_kind(self, repo):
        """P2-1：空 doc_id 取“最新一份 proposal”，跳过 interview_guide 等其他 kind。

        双后端一致：json 版曾直接取 [-1]，后续保存其他 kind 文档后空参
        确认会误报 wrong_kind，与 sqlite 版分歧。
        """
        repo.save_proposal("p1", "# 方案", kind="proposal", version="final")
        repo.save_proposal("p1", "# 访谈指南", kind="interview_guide", version="final")
        rec = repo.confirm_proposal("p1")   # 空 doc_id → 确认 proposal 而非报错
        confirmed = repo.get_proposal_by_id("p1", rec["subject"])
        assert confirmed["kind"] == "proposal"
        assert confirmed["content"] == "# 方案"

    # ---------------------------------------------------------------- 项目与状态面

    def test_create_is_idempotent(self, repo):
        repo.set_phase("p1", "field_site_profiles")
        repo.create("p1")  # 重复 create（= 打开存量项目）不得清空状态
        assert repo.get_phase("p1") == "field_site_profiles"

    def test_list_projects_summaries(self, repo):
        repo.create("p2")
        projects = repo.list_projects()
        assert [p["project_id"] for p in projects] == ["p1", "p2"]
        assert projects[0]["phase"] == "research_positioning"
        assert projects[0]["created_at"] and projects[0]["updated_at"]

    def test_state_shape(self, repo):
        state = repo.get_state("p1")
        assert state["project_id"] == "p1"
        for key in (
            "created_at",
            "updated_at",
            "phase",
            "phase_history",
            "owner",
            "research_context",
            "dialogue_context",
            "field_sites",
            "informants",
            "proposals",
            "confirmations",
            "proposal_confirmed",
        ):
            assert key in state, f"缺状态键 {key}"

    def test_get_state_returns_copy(self, repo):
        state = repo.get_state("p1")
        state["phase"] = "hacked"
        assert repo.get_state("p1")["phase"] == "research_positioning"

    def test_unknown_project_raises_keyerror(self, repo):
        with pytest.raises(KeyError):
            repo.get_state("nope")
        with pytest.raises(KeyError):
            repo.set_phase("nope", "field_site_profiles")

    def test_state_survives_reopen(self, repo_factory, tmp_path):
        first = repo_factory(tmp_path)
        first.create("p1")
        first.set_phase("p1", "field_site_profiles")
        first.append_message("p1", "user", "重启测试")
        again = repo_factory(tmp_path)
        assert again.get_phase("p1") == "field_site_profiles"
        assert again.get_messages("p1")[-1]["content"] == "重启测试"

    def test_delete_project_purges_domain_data(self, repo):
        """删除即整域清除：消息/记录/缓存/文档随项目消失，邻居项目不受影响，
        同名重建为全新状态（幂等 create 不得复活已删数据）。"""
        repo.append_message("p1", "user", "待删除")
        repo.save_field_site("p1", {"name": "喜洲"})
        repo.cache_search("p1", "仪式", "web_search", {"hits": 1})
        repo.save_proposal("p1", "# 方案", version="final")
        repo.create("p2")
        repo.delete_project("p1")
        assert [p["project_id"] for p in repo.list_projects()] == ["p2"]
        with pytest.raises(KeyError):
            repo.get_state("p1")
        with pytest.raises(KeyError):
            repo.get_messages("p1")
        with pytest.raises(KeyError):
            repo.list_field_sites("p1")
        # 同名重建 = 全新项目
        repo.create("p1")
        assert repo.get_phase("p1") == "research_positioning"
        assert repo.get_messages("p1") == []
        assert repo.list_field_sites("p1") == []
        assert repo.get_state("p1")["proposals"] == []

    def test_delete_unknown_project_raises_keyerror(self, repo):
        with pytest.raises(KeyError):
            repo.delete_project("nope")

    # ---------------------------------------------------------------- owner 归属（归属隔离）

    def test_create_owner_persists_and_idempotent_keeps(self, repo):
        repo.create("p_owned", owner="anon:abc")
        assert repo.get_state("p_owned")["owner"] == "anon:abc"
        repo.create("p_owned", owner="anon:other")   # 幂等打开：不覆盖已有归属
        assert repo.get_state("p_owned")["owner"] == "anon:abc"

    def test_list_projects_owner_three_states(self, repo):
        repo.create("p_mine", owner="anon:me")
        repo.create("p_other", owner="anon:other")
        repo.create("p_free")
        assert [p["project_id"] for p in repo.list_projects()] == [
            "p1", "p_free", "p_mine", "p_other"]   # 缺省全量（p1 为 fixture 建的无主项目）
        assert [p["project_id"] for p in repo.list_projects(owner="anon:me")] == ["p_mine"]
        assert [p["project_id"] for p in repo.list_projects(owner=None)] == ["p1", "p_free"]

    def test_set_owner_claims_releases_and_errors(self, repo):
        repo.set_owner("p1", "anon:me")              # 认领
        assert repo.get_state("p1")["owner"] == "anon:me"
        repo.set_owner("p1", "anon:me")              # 幂等：同值直接返回
        repo.set_owner("p1", None)                   # 退回无主
        assert repo.get_state("p1")["owner"] is None
        with pytest.raises(KeyError):
            repo.set_owner("nope", "anon:me")
        with pytest.raises(ValueError):
            repo.create("p_bad", owner="no-namespace")
        with pytest.raises(ValueError):
            repo.set_owner("p1", "Anon:UPPER")
