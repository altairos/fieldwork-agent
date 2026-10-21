# tests/test_web_state_tools.py
"""Web 回合受限状态写工具（审计 F03 方案②）：白名单过滤 + 回路集成。"""
import asyncio

import pytest
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.agent.state_tools import build_state_tools
from fieldwork_agent.web_host.turn_manager import TurnManager


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat_stream(self, messages, **kw):
        self.calls.append((list(messages), kw))
        return iter(self.script.pop(0))


class FakeFlyAI:
    def __getattr__(self, name):
        def _stub(**kw):
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name=f"{name}-结果", price=100.0)],
                total=1, cheapest=100.0, dearest=100.0))
        return _stub


@pytest.fixture
def env(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    tm = TurnManager(root=tmp_path / "projects")
    return repo, tm


def test_r05_unknown_field_rejected_atomically(env):
    """审计二 R05：白名单外键 + 空白值 → 整次拒绝（原子），绝不部分写入。"""
    repo, _ = env
    schemas, handlers = build_state_tools(repo, "p1")
    assert [s["function"]["name"] for s in schemas] == \
        ["update_research_context", "get_project_brief", "save_informants"]
    envl = handlers["update_research_context"](
        patch={"budget": "5万", "hacked": "x", "topic": "  "})
    assert not envl.ok
    assert envl.error.code == "invalid_arguments"
    assert "未知字段：hacked" in envl.error.message
    assert "topic 不得为空白字符串" in envl.error.message
    assert repo.get_research_context("p1").get("budget") is None


def test_bool_flag_persists(env):
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](
        patch={"interviews_not_applicable": True,
               "interviews_na_reason": "纯文献研究"})
    assert envl.ok
    ctx = repo.get_research_context("p1")
    assert ctx.get("interviews_not_applicable") is True
    assert ctx.get("interviews_na_reason") == "纯文献研究"


def test_empty_patch_rejected(env):
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](patch={"nope": 1})
    assert not envl.ok
    assert envl.error.code == "invalid_arguments"


def test_loop_state_tool_call_persists(env):
    """回路集成：LLM 发起 update_research_context 工具调用 → 状态落盘。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "update_research_context",
                                          "arguments": {"patch": {"budget": "5万",
                                                                   "scale": "两人同行"}}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已更新"},
         {"content": "已更新", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "预算改成5万，两人同行",
                         tm, llm, FakeFlyAI()))
    ctx = repo.get_research_context("p1")
    assert ctx.get("budget") == "5万" and ctx.get("scale") == "两人同行"


def test_r05_bool_type_strict(env):
    """审计二 R05：布尔字段收字符串 "true" → 拒绝（旧实现会当普通字符串收下）。"""
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](
        patch={"interviews_not_applicable": "true"})
    assert not envl.ok
    assert envl.error.code == "invalid_arguments"
    assert "interviews_not_applicable 须为布尔" in envl.error.message


def test_data_carries_updated_and_reason_for_card(env):
    """轮 2 症状②：data 携带 updated 字段名 + reason 依据，供前端状态卡渲染。

    前端 ToolCard 只消费 Envelope.data（Timeline 不传 args），reason 文本
    必须经 data 通道下发给卡片，否则状态写入只剩键名列表无依据说明。
    """
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](
        patch={"budget": "5万", "topic": "宗族礼生"}, reason="用户确认预算与主题")
    assert envl.ok
    assert envl.data["updated"] == ["budget", "topic"]
    assert envl.data["reason"] == "用户确认预算与主题"
    # reason 缺省时省略键：卡片不渲染空依据行，data 形状保持最小
    envl2 = handlers["update_research_context"](patch={"scale": "单人"})
    assert envl2.ok
    assert envl2.data == {"updated": ["scale"]}


def test_get_project_brief_returns_bounded_summary(env):
    """F04：get_project_brief 工具返回有界项目摘要（repo 状态透出）。"""
    repo, _ = env
    repo.update_research_context("p1", {"topic": "宗族礼生"})
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["get_project_brief"]()
    assert envl.ok
    assert "项目摘要：" in envl.data["brief"]
    assert "宗族礼生" in envl.data["brief"]


# ---------------------------------------------------------------- P0-2：候选落库

_CANDIDATES = [
    {"type": "村干部", "approach": "经村委引荐", "access_channel": "乡镇介绍信",
     "preparation": "研究简况一页纸", "cautions": "政绩叙事",
     "ethics": "化名", "question_domains": ["村史", "集体经济"]},
    {"type": "乡村教师", "approach": "课后交谈"},
    {"type": "返乡务工者", "approach": "熟人引路"},
]


def test_save_informants_persists_and_dedupes(env):
    """P0-2：suggest_informants 只出候选，save_informants 才真正写进项目；
    重复 type 幂等跳过，不制造重复记录。"""
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    handlers["save_informants"](candidates=_CANDIDATES)
    records = repo.list_informants("p1")
    assert [r["type"] for r in records] == ["村干部", "乡村教师", "返乡务工者"]
    assert [r["code"] for r in records] == ["INF-01", "INF-02", "INF-03"]
    # 候选七字段映射进五字段记录（approach→relation、access_channel→contact）
    assert records[0]["relation"] == "经村委引荐" and records[0]["contact"] == "乡镇介绍信"
    assert "提问域：村史、集体经济" in records[0]["notes"]

    again = handlers["save_informants"](candidates=_CANDIDATES)
    assert not again.ok and again.error.code == "nothing_saved"
    assert len(repo.list_informants("p1")) == 3


def test_save_informants_stages_in_turn_buffer(env):
    """注入 buffer 时与 ctx patch 同生命周期：commit 才落盘、discard 不落地。"""
    from fieldwork_agent.runtime.turn_buffer import TurnPatchBuffer

    repo, _ = env
    buffer = TurnPatchBuffer(repo, "p1")
    _, handlers = build_state_tools(repo, "p1", buffer=buffer)
    envl = handlers["save_informants"](candidates=_CANDIDATES[:2])
    assert envl.ok and envl.data["total"] == 2
    assert repo.list_informants("p1") == []          # 尚未提交
    buffer.discard()
    assert repo.list_informants("p1") == []          # 取消不落盘

    handlers["save_informants"](candidates=_CANDIDATES[:2])
    buffer.commit()
    assert [r["type"] for r in repo.list_informants("p1")] == ["村干部", "乡村教师"]


def test_save_informants_rejects_empty(env):
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["save_informants"](candidates=[])
    assert not envl.ok and envl.error.code == "invalid_arguments"
