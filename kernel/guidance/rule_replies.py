"""无 LLM 降级时的阶段规则回复文案（CLI / Web 双宿主共享）。

未配置 DashScope key（``Config.dashscope_available`` 为假）时宿主不接
LLMClient，回合回落到这里的固定引导文案；宿主各自负责降级的呈现方式
（CLI 启动横幅 / Web 回复内提示），文案本身单一来源。
"""
_RULE_REPLIES: dict[str, str] = {
    "research_positioning": (
        "感谢您的描述。为了帮您制定更精准的田野调查旅行方案，我需要了解几个关键信息：\n\n"
        "1. 您的学科背景和研究取向是什么？\n"
        "2. 初步的研究问题或关注焦点是什么？\n"
        "3. 您对田野地点的确定程度如何？\n\n"
        "请尽量详细地告诉我，我会根据您的回答逐步完善方案。"
    ),
    "field_site_profiles": (
        "好的，我来帮您收集候选田野点的详细信息。\n\n请告诉我您关注的目标区域或具体地点名称。"
    ),
    "itinerary_planning": (
        "了解了。现在我来为您规划详细的行程安排。\n\n请告诉我您的出发城市和预期的调研日期。"
    ),
    "proposal_generation": (
        "正在整合所有信息，生成完整的研究方案...\n\n"
        "方案将包含研究背景、方法论、详细行程、预算明细、风险评估等完整章节。"
    ),
}


def rule_based_reply(phase: str) -> str:
    """按阶段返回固定引导文案；未知阶段给通用兜底。"""
    return _RULE_REPLIES.get(phase, "收到，请继续说明您的需求。")
