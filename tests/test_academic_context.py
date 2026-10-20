"""RAG M1：角色保护、明确背景、更正/撤回与入口契约。全部离线。"""
import json
from pathlib import Path

import pytest
from fieldwork_agent.kernel.context_patch import context_patch_schema, sanitize_context_patch
from fieldwork_agent.kernel.guidance.brief import build_project_brief
from fieldwork_agent.kernel.guidance.extract import apply_message_extractions
from fieldwork_agent.kernel.models import ACADEMIC_CONTEXT_FIELDS, ResearchContext
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository
from fieldwork_agent.runtime.state_tools import build_project_state_tools


@pytest.mark.parametrize("case", json.loads((Path(__file__).parent / "fixtures/rag/academic-context-v1.json").read_text(encoding="utf-8"))["cases"])
def test_role_aware_extraction(case):
    patch = apply_message_extractions(case["message"], case["existing"])
    expected = case["expected"]
    assert {key: patch.get(key) for key in expected} == expected
    for key in case["forbidden"]:
        assert key not in patch


def test_explicit_academic_correction_and_withdrawal():
    current = {"research_orientation": "华南学派", "target_institution": "香港中文大学",
               "target_advisor": "陈老师", "target_location": "香港", "extras": {"region": "华南"}}
    patch = apply_message_extractions("取向改为历史人类学，目标院校改为北京大学，取消目标导师。", current)
    assert patch == {"research_orientation": "历史人类学", "target_institution": "北京大学", "target_advisor": ""}
    # 不自动清理既有地点：它们可能来自用户真实确认。
    assert current["target_location"] == "香港" and current["extras"]["region"] == "华南"


@pytest.mark.parametrize("message", [
    "不把取向从华南学派改为历史人类学。",
    "别取消申请香港中文大学。",
    "老师说，目标院校是北京大学。",
    "如果有机会，目标院校是北京大学。",
    "我读过《目标院校是北京大学》。",
])
def test_negated_reported_and_hypothetical_background_does_not_write(message):
    current = {"research_orientation": "华南学派", "target_institution": "香港中文大学",
               "target_department": "历史系", "target_advisor": "陈老师"}
    patch = apply_message_extractions(message, current)
    assert not set(patch) & set(ACADEMIC_CONTEXT_FIELDS)


def test_school_alias_and_target_department_do_not_fill_fieldwork_or_discipline():
    patch = apply_message_extractions("想申请香港中大社会学系。")
    assert patch["target_institution"] == "香港中大"
    assert not {"target_location", "extras.region", "discipline"} & patch.keys()
    patch = apply_message_extractions("目标院系是社会学系。")
    assert patch == {"target_department": "社会学系"}


def test_explicit_self_assertion_after_reported_suggestion_can_be_saved():
    patch = apply_message_extractions("老师建议我申请北京大学，但我的目标院校是香港中文大学。")
    assert patch["target_institution"] == "香港中文大学"


@pytest.mark.parametrize("patch", [
    {"clear_fields": []}, {"clear_fields": "target_advisor"}, {"clear_fields": [None]},
    {"clear_fields": ["topic"]}, {"clear_fields": ["target_advisor", "target_advisor"]},
    {"clear_fields": ["target_advisor"], "target_advisor": "张老师"},
    {"target_advisor": ""}, {"target_institution": None},
])
def test_invalid_academic_writes_are_atomic(patch):
    with pytest.raises(ValueError):
        sanitize_context_patch({"budget": "5万", **patch})


@pytest.mark.parametrize("repo_cls", [JsonRepository, SqliteRepository])
def test_academic_fields_round_trip_and_clear(tmp_path, repo_cls):
    repo = (repo_cls(projects_dir=tmp_path / "projects") if repo_cls is JsonRepository
            else repo_cls(db_path=tmp_path / "projects.sqlite"))
    pid = "academic"
    repo.create(pid)
    _, handlers = build_project_state_tools(repo, pid)
    patch = dict(zip(ACADEMIC_CONTEXT_FIELDS, ["华南学派", "中大", "历史系", "博士", "陈老师"], strict=True))
    assert handlers["update_research_context"](patch).ok
    ctx = repo.get_research_context(pid)
    assert ResearchContext.model_validate(ctx).target_institution == "中大"
    assert "target_institution=中大" in build_project_brief(repo.get_state(pid))
    assert handlers["update_research_context"]({"clear_fields": ["target_advisor"]}).ok
    assert repo.get_research_context(pid)["target_advisor"] == ""
    assert "clear_fields" not in repo.get_research_context(pid)
    before = repo.get_research_context(pid)
    assert not handlers["update_research_context"]({"budget": "1万", "target_advisor": ""}).ok
    assert repo.get_research_context(pid) == before


def test_academic_schema_matches_rest_and_model():
    from fieldwork_agent.web_host.api.state import ContextPatch

    schema = context_patch_schema()["properties"]
    assert set(ACADEMIC_CONTEXT_FIELDS) <= set(ResearchContext.model_fields)
    assert set(schema) == set(ContextPatch.model_fields)
    assert set(schema["clear_fields"]["items"]["enum"]) == set(ACADEMIC_CONTEXT_FIELDS)


def test_cli_and_web_extraction_keep_the_same_academic_state(tmp_path):
    from fieldwork_agent.core.orchestrator import FieldAgent
    from fieldwork_agent.web_host.agent.loop import _apply_extractions

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    agent = FieldAgent.__new__(FieldAgent)
    agent.repo = repo
    for pid in ("cli", "web"):
        repo.create(pid)
    for message in ("取向是华南学派，申请香港中文大学历史系。", "目标院校改为中大，取消研究取向。"):
        agent._apply_extract("cli", message)
        _apply_extractions(repo, "web", message)
    cli, web = (repo.get_research_context(pid) for pid in ("cli", "web"))
    assert cli == web
    assert cli["target_institution"] == "中大" and cli["research_orientation"] == ""
    assert not cli.get("target_location") and not (cli.get("extras") or {}).get("region")


def test_clearing_staged_academic_field_can_be_cancelled(tmp_path):
    from fieldwork_agent.runtime.turn_buffer import TurnPatchBuffer

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    repo.update_research_context("p1", {"target_advisor": "陈老师"})
    buffer = TurnPatchBuffer(repo, "p1")
    _, handlers = build_project_state_tools(repo, "p1", buffer=buffer)
    assert handlers["update_research_context"]({"clear_fields": ["target_advisor"]}).ok
    assert buffer.staged_view()["target_advisor"] == ""
    assert repo.get_research_context("p1")["target_advisor"] == "陈老师"
    buffer.discard()
    assert buffer.staged_view()["target_advisor"] == "陈老师"
