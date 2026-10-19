"""无 LLM 降级时的阶段规则回复文案（CLI / Web 双宿主共享）。

未配置 DashScope key（``Config.dashscope_available`` 为假）时宿主不接
LLMClient，回合回落到这里的规则引导文案；宿主各自负责降级的呈现方式
（CLI 启动横幅 / Web 回复内提示），文案本身单一来源。
"""
from fieldwork_agent.kernel.guidance.research import task_readiness

_RULE_REPLIES: dict[str, str] = {
    "proposal_generation": (
        "研究方案可按研究背景、问题、方法、行程、预算与风险评估整理。"
        "当前只能提供规则引导；完整草稿需配置模型后生成，引用和具体供给数据仍需核验。"
    ),
    "finished": "已有方案可作为当前研究设计的基础，后续可按具体需要修改。",
}


def rule_based_reply(phase: str, context: dict | None = None) -> str:
    """无模型时只选一个待补问题；复用已有字段，不宣称完成智能归纳或实际检索。"""
    ctx = context or {}

    def known(key: str) -> bool:
        return bool(ctx.get(key) or (ctx.get("extras") or {}).get(key))

    if phase == "research_positioning":
        if not known("topic"):
            return "可以先从具体线索开始。有没有一件经历，或者一份读过的材料，让您产生研究兴趣？"
        if not known("research_questions"):
            return "围绕已记录的研究主题，您目前最想弄清楚的具体困惑是什么？"
        if not task_readiness(ctx, "field_site_comparison")["ready"]:
            return "当前主题与已有问题需要重新核对；您现在最想解释的具体关系或过程是什么？"
        return "主题与核心问题的基础信息已具备，可据此继续探索文献、田野点与材料路径。"
    if phase == "field_site_profiles":
        if not known("target_location"):
            return "您目前关注的目标区域或具体地点是什么？"
        return "已记录目标地点，可据此整理研究进入条件与生活保障；具体地理和供给信息仍待工具核验。"
    if phase == "itinerary_planning":
        if not known("departure_city"):
            return "您的出发城市是哪里？"
        if not known("travel_date") and not known("time_window"):
            return "您希望在什么日期出发？"
        return "已记录出发地和调研时间；具体交通、住宿与费用需取得真实供给数据后核验。"
    return _RULE_REPLIES.get(phase, "已记录本轮说明，可据此继续完善研究方案。")
