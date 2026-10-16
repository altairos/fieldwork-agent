"""覆盖端到端：Tier S 假包切换后，guidance/documents/mcp 全链路动态生效。"""
import json

import pytest
from fieldwork_agent.kernel import assets
from fieldwork_agent.kernel.documents.informants import suggest_informants
from fieldwork_agent.kernel.documents.proposal import compose_interview_guide
from fieldwork_agent.kernel.guidance import informants, phases
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server import resources

PHASE_KEYS = [
    "research_positioning", "field_site_profiles", "itinerary_planning",
    "proposal_generation", "finished",
]
FAKE_LIBRARY = {
    "测试甲": {"type": "测试甲", "approach": "甲式接触", "cautions": "甲注意",
             "question_domains": ["域一"], "ethics": "甲伦理",
             "access_channel": "甲渠道", "preparation": "甲准备",
             "examples": ["问一", "问二", "追问：三"]},
    "测试乙": {"type": "测试乙", "approach": "乙式接触", "cautions": "乙注意",
             "question_domains": ["域二"], "ethics": "乙伦理",
             "access_channel": "乙渠道", "preparation": "乙准备",
             "examples": ["问一", "问二", "追问：三"]},
}
FAKE_PROMPTS = {k: f"假-{k}" for k in PHASE_KEYS}
FAKE_GENERATION = {
    "suggest_system": "假生成-suggest_system",
    "guide_system": "假生成-guide_system",
    "guide_instruction": "假生成-guide_instruction",
}


class FakeLLM:
    def __init__(self, out):
        self.out = out
        self.messages = []

    def chat(self, messages, **kwargs):
        self.messages.extend(list(messages))
        return self.out


@pytest.fixture(autouse=True)
def _restore_assets(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assets.reload(empty)
    yield
    assets.reload(empty)


@pytest.fixture
def fake_pkg(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "informant_library.json").write_text(
        json.dumps(FAKE_LIBRARY, ensure_ascii=False), encoding="utf-8")
    (pkg / "example_types.json").write_text(
        json.dumps(["测试甲"], ensure_ascii=False), encoding="utf-8")
    (pkg / "interview_methodology.txt").write_text(
        "# 端到端假方法论", encoding="utf-8")
    (pkg / "system_prompts.json").write_text(
        json.dumps(FAKE_PROMPTS, ensure_ascii=False), encoding="utf-8")
    (pkg / "generation_prompts.json").write_text(
        json.dumps(FAKE_GENERATION, ensure_ascii=False), encoding="utf-8")
    return pkg


def test_informants_module_switches(fake_pkg):
    assets.reload(fake_pkg)
    assert set(informants.INFORMANT_LIBRARY) == {"测试甲", "测试乙"}
    assert set(informants.INFORMANT_EXAMPLES) == {"测试甲"}
    assert informants.get_informant("测试甲")["approach"] == "甲式接触"
    assert informants.get_informant("虚构访谈对象") is None
    assert informants.INTERVIEW_GUIDE_METHODOLOGY == "# 端到端假方法论"


def test_phases_module_switches(fake_pkg):
    assets.reload(fake_pkg)
    assert phases.SYSTEM_PROMPTS["itinerary_planning"] == "假-itinerary_planning"
    assert phases.phase_prompt("finished") == "假-finished"
    assert phases.phase_prompt("unknown-phase") == "假-research_positioning"


def test_match_examples_fallback_on_fake_pkg(fake_pkg):
    assets.reload(fake_pkg)
    out = informants.match_examples("完全无法匹配的主题 qwerty")
    # 全落空兜底：返回全部生效示范（假包仅"测试甲"）
    assert [o["type"] for o in out] == ["测试甲"]


def test_suggest_informants_uses_pkg_system(fake_pkg):
    assets.reload(fake_pkg)
    llm = FakeLLM({"content": "[]"})
    suggest_informants("主题", llm=llm)
    assert llm.messages[0]["role"] == "system"
    assert llm.messages[0]["content"] == "假生成-suggest_system"


def test_compose_interview_guide_uses_pkg(fake_pkg):
    assets.reload(fake_pkg)
    llm = FakeLLM({"content": "提纲正文"})
    guides = compose_interview_guide("主题", ["测试甲"], llm=llm)
    assert llm.messages[0]["role"] == "system"
    assert llm.messages[0]["content"] == "假生成-guide_system"
    user = llm.messages[1]["content"]
    assert "# 端到端假方法论" in user
    assert "假生成-guide_instruction" in user
    assert guides["测试甲"] == "提纲正文"


def test_resources_dynamic_vs_snapshot(fake_pkg, tmp_path):
    before = [r.uri for r in resources.STATIC_RESOURCES]
    assets.reload(fake_pkg)
    # STATIC_RESOURCES 是 import 时快照，不随 reload 变
    assert [r.uri for r in resources.STATIC_RESOURCES] == before
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    out = resources.read_resource(repo, "fieldwork://guidance/informants/library")
    assert out["ok"] and "测试甲" in out["text"]
    method = resources.read_resource(
        repo, "fieldwork://guidance/interview-guide/methodology")
    assert method["ok"] and "# 端到端假方法论" in method["text"]
