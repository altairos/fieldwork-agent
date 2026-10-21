"""回合内上下文预算：工具结果累计超限时压缩最早条目（P0 上下文撑爆治理）。

单条工具结果已由 ``runtime.loop._tool_content`` 截到安全上限（R5：检索类
2000 字符、文献 6000），但一个回合最多 20 次调用——20×2000 字符仍可撑爆
模型上下文。本模块在**每次发起
LLM 调用前**按累计字符预算从最早的 tool 消息开始压缩：

- 最近 ``keep_recent`` 条完整保留（当前推理最依赖它们）；
- 被压缩条目的 ``tool_call_id`` 不动，只替换 ``content`` 为「预览 + 占位」，
  OpenAI 协议上每个 tool_call 仍有对应回填；
- 幂等：已含占位标记的条目不重复压缩；
- 只动 ``role=tool``，system/user/assistant 消息一律不碰。
"""
from typing import Any

# 单回合全部工具结果 content 的累计预算（字符）。固定开销（系统提示+工具
# schema ≈ 3.5 万字符）与历史（30 条）之外的安全余量。
TURN_TOOL_CONTENT_BUDGET = 30000
# 最近若干条工具结果完整保留
TURN_TOOL_KEEP_RECENT = 2
# 压缩时保留的预览字符数
_PREVIEW_CHARS = 200
# P2-5：占位文案不再声称“已落盘、可在证据面板查看”——state 工具（如
# get_project_brief/suggest_informants）结果并不写 cached_searches，那句话对它们
# 不成立，会把模型引向一个不存在的证据条目。改写查询也是与重复查询守卫对齐的
# 唯一可操作路径。
TOOL_COMPACT_MARK = "〔已压缩：完整结果已移出上下文；如仍需数据，请改写查询条件后重新获取〕"


def trim_turn_messages(messages: list[dict[str, Any]], *,
                       budget_chars: int = TURN_TOOL_CONTENT_BUDGET,
                       keep_recent: int = TURN_TOOL_KEEP_RECENT) -> int:
    """就地压缩最早的 tool 消息；返回压缩条数（未超预算返回 0）。

    ``messages`` 为宿主回合内的消息列表（含 system/user/assistant/tool）。
    """
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if not tool_indices:
        return 0
    compactable = (tool_indices[:-keep_recent] if keep_recent > 0 else tool_indices)
    total = sum(len(str(messages[i].get("content") or "")) for i in tool_indices)
    compacted = 0
    for i in compactable:
        if total <= budget_chars:
            break
        content = str(messages[i].get("content") or "")
        if not content or TOOL_COMPACT_MARK in content:
            continue
        placeholder = content[:_PREVIEW_CHARS] + "\n" + TOOL_COMPACT_MARK
        # P2-5：短结果压成固定长度占位反而更长——跳过，保持“压缩只减不增”的
        # 不变量（旧实现会把 total 越压越大，compacted 计数也与真实收益脱节）。
        if len(placeholder) >= len(content):
            continue
        messages[i]["content"] = placeholder
        total -= len(content) - len(placeholder)
        compacted += 1
    return compacted
