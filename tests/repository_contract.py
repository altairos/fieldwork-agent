"""L2 repository 契约套件 — 每个 ``ProjectRepository`` 实现都必须全绿（spec §5.1）。

用法：具体实现的测试文件 ``from repository_contract import TestContract, repo,
repo_factory`` 即继承全部用例；Plan 5 把 ``make_sqlite_repo`` 追加进
``REPO_FACTORIES`` 就变成 Json/Sqlite 双跑，**本文件零改动**。

实现中立纪律：
- 用例只经 Protocol 方法访问被测实现；唯一例外是 64KB 截断用例需要直读落盘文件，
  借用实现自带的 ``root`` 属性（视为实现细节，不得在其他用例出现）；
- 落盘布局、文件名、编码方式等 Json 专属假设一律留在 ``tests/test_json_repo.py``。
"""

import json

import pytest
from fieldwork_agent.kernel.guidance.phases import PHASES
from fieldwork_agent.kernel.repository.json_repo import JsonRepository


def make_json_repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


REPO_FACTORIES = [make_json_repo]


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
        full = repo.get_cached_search(cid)
        assert full["query"] == "大理住宿"
        assert full["tool"] == "search_hotel"
        assert full["result"] == {"items": [1, 2]}
        assert [c["id"] for c in repo.list_cached_searches("p1")] == [cid]
        assert repo.list_cached_searches("p1")[0]["query"] == "大理住宿"

    def test_unknown_cache_id_returns_none(self, repo):
        assert repo.get_cached_search("no_such_id") is None

    def test_cache_truncated_64kb(self, repo):
        big = {"blob": "x" * 100_000}
        cid = repo.cache_search("p1", "q", "search_ai", big)
        stored = json.loads(
            (repo.root / "p1" / "search_cache" / f"{cid}.json").read_text(encoding="utf-8")
        )
        # 超限后 result 被整体替换为截断标记（Interfaces / Step 3 / 全局约束三处一致）
        assert stored["result"]["truncated"] is True
        assert len(stored["result"]["preview"]) <= 64 * 1024

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
            "research_context",
            "dialogue_context",
            "field_sites",
            "informants",
            "proposals",
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
