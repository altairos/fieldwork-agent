"""F04：CLI 宿主同步 Web 侧已修复的确认/终态语义（首批审计遗留收口）。

Web 侧（web_host/api/state.py）早已具备：田野点确认、方案**显式 doc_id**
确认（R09 不隐式取最新一份）、四重校验错误映射、确认后确定性推进。CLI 宿主
此前没有任何 confirm 入口——用户在 CLI 里永远无法走 D→finished 终态。
本文件锚定补齐后的 CLI 语义与 Web 侧平价：

- ``confirm``（无参数）：确认概览——待确认田野点、可确认方案清单、stale 提示；
- ``confirm site <site_id>``：repo.confirm_field_site + 确定性推进；
- ``confirm proposal <doc_id>``：缺 doc_id 列清单提示（R09 对齐，不隐式确认）；
  ProposalConfirmError 四类 code 落地为中文错误；成功后推进（D→finished 可达）；
- ``status``：补方案确认状态行（含过期提示）；
- ``FieldAgent.advance_phase()``：公开推进入口（对齐 web 的 check_and_advance）。
"""

from fieldwork_agent.core.cli import _print_status, _run_confirm
from fieldwork_agent.core.orchestrator import FieldAgent

# A→B 推进门槛：七字段全齐（MIN_POSITIONING_ANSWERED = 7）
_FULL_CTX = {
    "purpose": "kaiti", "discipline": "人类学", "topic": "宗族", "scale": "单人",
    "budget": "2万", "time_window": "7月", "research_questions": "宗族礼生角色",
}


def make_agent(fake_config) -> FieldAgent:
    agent = FieldAgent(fake_config)
    agent.create_project("p1")
    return agent


# ------------------------------------------------------- advance_phase 公开包装


def test_advance_phase_is_public_wrapper(fake_config):
    agent = make_agent(fake_config)
    agent.repo.update_research_context("p1", dict(_FULL_CTX))
    t = agent.advance_phase()
    assert t == {"from": "research_positioning", "to": "field_site_profiles"}
    assert agent.repo.get_phase("p1") == "field_site_profiles"


def test_advance_phase_without_project_returns_none(fake_config):
    agent = FieldAgent(fake_config)
    assert agent.advance_phase() is None


# ---------------------------------------------------------------- confirm 概览


def test_confirm_without_active_project(fake_config, capsys):
    agent = FieldAgent(fake_config)
    _run_confirm(agent, "")
    assert "没有活跃项目" in capsys.readouterr().out


def test_confirm_overview_lists_pending_sites_and_proposals(fake_config, capsys):
    agent = make_agent(fake_config)
    sid = agent.repo.save_field_site("p1", {"name": "喜洲镇", "confirmed": False})
    doc = agent.repo.save_proposal("p1", "# 方案", version="draft")
    _run_confirm(agent, "")
    out = capsys.readouterr().out
    assert sid in out and "喜洲镇" in out          # 待确认田野点（id + 名称）
    assert doc in out                               # 可确认方案（doc_id）
    assert "confirm site" in out and "confirm proposal" in out


def test_confirm_overview_flags_stale_confirmation(fake_config, capsys):
    agent = make_agent(fake_config)
    doc = agent.repo.save_proposal("p1", "# v1", version="draft")
    agent.repo.confirm_proposal("p1", doc)
    agent.repo.save_proposal("p1", "# v2", version="draft")   # 新草稿 → stale
    _run_confirm(agent, "")
    assert "过期" in capsys.readouterr().out


# ---------------------------------------------------------------- confirm site


def test_confirm_site_confirms_and_advances(fake_config, capsys):
    agent = make_agent(fake_config)
    agent.repo.update_research_context("p1", dict(_FULL_CTX))
    agent.repo.update_research_context("p1", {"interviews_not_applicable": True})
    sid = agent.repo.save_field_site("p1", {"name": "喜洲镇", "confirmed": False})
    _run_confirm(agent, f"site {sid}")
    out = capsys.readouterr().out
    assert "已确认" in out
    assert "研究定位 → 田野点画像" in out            # A→B 已满足，B→C 即刻推进
    assert agent.repo.list_field_sites("p1")[0]["confirmed"] is True


def test_confirm_site_unknown_id(fake_config, capsys):
    agent = make_agent(fake_config)
    _run_confirm(agent, "site nope")
    assert "不存在" in capsys.readouterr().out


# ------------------------------------------------------------- confirm proposal


def test_confirm_proposal_without_doc_id_lists_docs(fake_config, capsys):
    agent = make_agent(fake_config)
    agent.repo.save_proposal("p1", "# v1", version="draft")
    _run_confirm(agent, "proposal")
    out = capsys.readouterr().out
    assert "doc_id" in out or "文档 ID" in out
    # R09 对齐：缺 doc_id 不隐式确认最新一份
    assert not agent.repo.get_state("p1").get("proposal_confirmed")


def test_confirm_proposal_success_advances_to_finished(fake_config, capsys):
    """F04 核心：CLI 侧 D→finished 终态经显式确认可达。"""
    agent = make_agent(fake_config)
    agent.repo.set_phase("p1", "proposal_generation")
    doc = agent.repo.save_proposal("p1", "# 方案", version="draft")
    _run_confirm(agent, f"proposal {doc}")
    out = capsys.readouterr().out
    assert "已确认" in out
    assert "方案生成 → 已完成" in out
    assert agent.repo.get_phase("p1") == "finished"


def test_confirm_proposal_dep_hash_mismatch_shows_error(fake_config, capsys):
    agent = make_agent(fake_config)
    agent.repo.set_phase("p1", "proposal_generation")
    doc = agent.repo.save_proposal("p1", "# 方案", version="draft")
    agent.repo.update_research_context("p1", {"topic": "宗族组织变迁"})  # 依赖已变
    _run_confirm(agent, f"proposal {doc}")
    out = capsys.readouterr().out
    assert "重新生成" in out
    assert not agent.repo.get_state("p1").get("proposal_confirmed")
    assert agent.repo.get_phase("p1") == "proposal_generation"   # 不推进


def test_confirm_proposal_unknown_doc_shows_error(fake_config, capsys):
    agent = make_agent(fake_config)
    _run_confirm(agent, "proposal ghost")
    assert "不存在" in capsys.readouterr().out


# ---------------------------------------------------------------- status 确认行


def test_status_shows_confirmation_state(fake_config, capsys):
    agent = make_agent(fake_config)
    _print_status(agent)
    assert "方案确认: 否" in capsys.readouterr().out

    agent.repo.set_phase("p1", "proposal_generation")
    doc = agent.repo.save_proposal("p1", "# 方案", version="draft")
    agent.repo.confirm_proposal("p1", doc)
    _print_status(agent)
    assert "方案确认: 是" in capsys.readouterr().out

    agent.repo.save_proposal("p1", "# v2", version="draft")   # stale
    _print_status(agent)
    assert "方案确认: 是（已过期" in capsys.readouterr().out
