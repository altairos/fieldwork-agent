# tests/test_turn_context_budget.py
"""回合内上下文预算与文档工具 schema 瘦身（P0：上下文撑爆治理）。

两类行为：
- ``trim_turn_messages``：单个回合内工具结果累计超预算时，从最早的开始压缩，
  保留最近若干条完整、保留协议字段（tool_call_id）——防止 20 次调用把上下文撑爆；
- ``compact_doc_tool_schemas``：web 宿主下发给模型的 compose_proposal schema
  不再暴露宿主会自动填充的五渠道大对象，减少巨型参数与校验失败重试。
"""
from fieldwork_agent.kernel.registry import build_tool_schemas
from fieldwork_agent.runtime.context_budget import (
    TOOL_COMPACT_MARK,
    trim_turn_messages,
)
from fieldwork_agent.runtime.doc_args import compact_doc_tool_schemas


def _tool_msg(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_trim_turn_messages_compacts_oldest_tool_results():
    msgs = [{"role": "system", "content": "系统提示"},
            {"role": "user", "content": "查一下"}]
    msgs += [_tool_msg(f"c{i}", "x" * 6000) for i in range(6)]

    compacted = trim_turn_messages(msgs, budget_chars=12000, keep_recent=2)

    # 6 条 × 6000 字符，保留最近 2 条 → 最早的 4 条被压缩
    assert compacted == 4
    assert len(msgs[2]["content"]) < 1000
    assert TOOL_COMPACT_MARK in msgs[2]["content"]
    # 最近 2 条完整
    assert msgs[-1]["content"] == "x" * 6000
    assert msgs[-2]["content"] == "x" * 6000
    # 协议字段与非 tool 消息不动
    assert [m["tool_call_id"] for m in msgs[2:]] == [f"c{i}" for i in range(6)]
    assert msgs[0]["content"] == "系统提示"


def test_trim_turn_messages_noop_within_budget():
    msgs = [_tool_msg("c1", "a" * 500), _tool_msg("c2", "b" * 500)]
    assert trim_turn_messages(msgs, budget_chars=2000, keep_recent=2) == 0
    assert msgs[0]["content"] == "a" * 500


def test_trim_turn_messages_is_idempotent():
    msgs = [_tool_msg(f"c{i}", "x" * 6000) for i in range(4)]
    assert trim_turn_messages(msgs, budget_chars=6000, keep_recent=1) == 3
    snapshot = [m["content"] for m in msgs]

    # 二次调用不再重复压缩（占位本身不再计费/再截断）
    assert trim_turn_messages(msgs, budget_chars=6000, keep_recent=1) == 0
    assert [m["content"] for m in msgs] == snapshot


def test_trim_turn_messages_never_drops_protocol_fields():
    msgs = [_tool_msg(f"c{i}", "y" * 5000) for i in range(5)]
    trim_turn_messages(msgs, budget_chars=0, keep_recent=0)
    assert all(set(m) == {"role", "tool_call_id", "content"} for m in msgs)
    assert all(m["content"] for m in msgs)   # 占位非空：API 不接受空 tool 内容


def test_trim_turn_messages_does_not_inflate_short_results():
    """P2-5：短结果压成固定占位反而更长——必须跳过（探针 before=150 after=218）。"""
    msgs = [_tool_msg("c1", "a" * 50), _tool_msg("c2", "b" * 100),
            _tool_msg("c3", "c" * 300)]
    before = [m["content"] for m in msgs]

    # 预算 0 强制触发压缩：前两条短于占位长度 → 跳过；第三条变短才算压缩
    compacted = trim_turn_messages(msgs, budget_chars=0, keep_recent=0)

    assert msgs[0]["content"] == before[0]
    assert msgs[1]["content"] == before[1]
    assert TOOL_COMPACT_MARK in msgs[2]["content"]
    assert len(msgs[2]["content"]) < len(before[2])
    assert compacted == 1
    assert sum(len(m["content"]) for m in msgs) <= sum(len(c) for c in before)


def test_compact_doc_tool_schemas_hides_host_filled_channels():
    compacted = compact_doc_tool_schemas(build_tool_schemas())
    proposal = next(s for s in compacted
                    if s["function"]["name"] == "compose_proposal")
    props = proposal["function"]["parameters"]["properties"]

    # 五渠道大对象不再出现在模型可见 schema 里（由宿主 merge_doc_args 填充）
    for hidden in ("ctx", "sites", "itinerary", "informants", "knowledge"):
        assert hidden not in props
    # 模型仍需决定的短参数保留
    assert "section" in props and "mode" in props and "prior_markdown" in props
    assert "自动填充" in proposal["function"]["description"]


def test_compact_doc_tool_schemas_does_not_mutate_source():
    raw = build_tool_schemas()
    proposal = next(s for s in raw if s["function"]["name"] == "compose_proposal")

    compact_doc_tool_schemas(raw)

    # MCP/CLI 共用 registry 单一来源，原始 schema 不得被就地改写
    assert "ctx" in proposal["function"]["parameters"]["properties"]
