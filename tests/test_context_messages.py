# tests/test_context_messages.py
"""历史消息组装预算：条数 + 字符双限（P1-a）。

长会话（30 条 × 1–2k 字）会把 prompt 推高数万字符；这里验证 build_messages
按预算从最新向旧保留，当前 user 消息与选项提醒始终追加。
"""
import pytest
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.agent.context import HISTORY_CHAR_BUDGET, build_messages


@pytest.fixture()
def repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


def _chat_msg(role: str, idx: int, size: int = 5000) -> str:
    return f"{role}{idx}:" + "x" * size


def test_build_messages_caps_history_by_char_budget(repo):
    """20 条各 5000 字符 → 只保留能装进预算的最近若干条（时间正序）。"""
    repo.create("p")
    for i in range(10):
        repo.append_message("p", "user", _chat_msg("u", i))
        repo.append_message("p", "assistant", _chat_msg("a", i))

    msgs = build_messages(repo, "p", "当前问题", "research_positioning")

    history = msgs[1:-2]          # 去掉 system / 当前 user / options reminder
    assert history
    assert sum(len(m["content"]) for m in history) <= HISTORY_CHAR_BUDGET
    # 最新一条在、最旧的被裁掉
    assert history[-1]["content"].startswith("a9:")
    assert not any(m["content"].startswith("u0:") for m in history)
    # 时间正序不变（role 前缀后的序号单调不减）
    indices = [int(m["content"][1]) for m in history]
    assert indices == sorted(indices)
    # 当前消息与选项提醒始终在尾部
    assert msgs[-2] == {"role": "user", "content": "当前问题"}
    assert msgs[-1]["role"] == "system"


def test_build_messages_keeps_short_history(repo):
    """短历史原样保留（预算内不做任何裁剪）。"""
    repo.create("p")
    repo.append_message("p", "user", "hello")
    repo.append_message("p", "assistant", "hi")

    msgs = build_messages(repo, "p", "q", "research_positioning")

    assert msgs[1] == {"role": "user", "content": "hello"}
    assert msgs[2] == {"role": "assistant", "content": "hi"}
    assert msgs[-2]["content"] == "q"


def test_build_messages_always_keeps_min_recent_messages(repo, monkeypatch):
    """预算极小时仍至少保留最近 HISTORY_MIN_MESSAGES 条（上下文不能全空）。"""
    from fieldwork_agent.web_host.agent import context as context_mod
    monkeypatch.setattr(context_mod, "HISTORY_CHAR_BUDGET", 1)
    repo.create("p")
    for i in range(6):
        repo.append_message("p", "user", f"u{i}")
        repo.append_message("p", "assistant", f"a{i}")

    msgs = build_messages(repo, "p", "q", "research_positioning")

    history = msgs[1:-2]
    assert len(history) >= context_mod.HISTORY_MIN_MESSAGES
    # 保留的必须是最近的，不丢最新上下文
    assert history[-1]["content"] == "a5"


def test_build_messages_filters_non_dialogue_roles(repo, monkeypatch):
    """P3-6：旧史里混入的 system/tool 记录不得进 prompt（只留 user/assistant）。"""
    repo.create("p")
    monkeypatch.setattr(repo, "get_messages", lambda pid, limit=30: [
        {"role": "system", "content": "旧系统提示"},
        {"role": "user", "content": "q1"},
        {"role": "tool", "content": "工具中间结果"},
        {"role": "assistant", "content": "a1"},
    ])

    msgs = build_messages(repo, "p", "当前问题", "research_positioning")

    history = msgs[1:-2]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert [m["content"] for m in history] == ["q1", "a1"]
