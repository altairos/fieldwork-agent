"""阶段流转与引导策略 — 从 ``core/dialogue_engine.py`` 原文迁移的纯数据。

约定：
- ``PHASES`` 五阶段顺序即推进顺序，值与旧 ``ProjectState.PHASE_*`` 常量完全一致；
- ``GUIDING_QUESTIONS`` 迁移自引擎原文；``SYSTEM_PROMPTS`` 已改为经 ``kernel/assets`` 分级加载的动态属性（Tier P 公开版内置，secrets/ 运行时覆盖）；
  旧引擎只为前三个阶段写了引导问题，此处为 D/完成两阶段补齐，使
  ``GUIDING_QUESTIONS`` 的键覆盖 ``PHASES``（避免宿主拿到空列表时无话可问）；
- ``check_advance`` 是确定性纯函数：输入全部由调用方传入、无 I/O、绝不调用 LLM
  （设计规格 §3.3：阶段推进判定不得由 LLM 宣布，防幻觉推进）。
"""

from datetime import date
from typing import Any

from fieldwork_agent.kernel import assets
from fieldwork_agent.kernel.guidance.research import task_readiness
from fieldwork_agent.kernel.models import (
    AdvanceResult,
    FieldSiteRecord,
    InformantRecord,
    ResearchContext,
)

# ---------------------------------------------------------------- 阶段常量

PHASE_RESEARCH_POSITIONING = "research_positioning"
PHASE_FIELD_SITE_PROFILES = "field_site_profiles"
PHASE_ITINERARY_PLANNING = "itinerary_planning"
PHASE_PROPOSAL_GENERATION = "proposal_generation"
PHASE_FINISHED = "finished"

PHASES: list[str] = [
    PHASE_RESEARCH_POSITIONING,
    PHASE_FIELD_SITE_PROFILES,
    PHASE_ITINERARY_PLANNING,
    PHASE_PROPOSAL_GENERATION,
    PHASE_FINISHED,
]

# 定位→田野探索只要求主题与明确的问题；执行约束按任务需要补充。
MIN_POSITIONING_ANSWERED = 2


def __getattr__(name: str):
    """PEP 562：SYSTEM_PROMPTS 动态代理到 assets（旧 import 得到快照）。"""
    if name == "SYSTEM_PROMPTS":
        return assets.system_prompts()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# 各阶段的引导问题素材：按当前缺口选用，不代表必须按序询问。
GUIDING_QUESTIONS: dict[str, list[str]] = {
    PHASE_RESEARCH_POSITIONING: [
        "这份方案主要用在什么场合？（海外/港澳申请、高校开题、基金申报、课程作业）",
        "请问您的学科背景和研究取向是什么？（人类学/社会学/政治学/跨学科？）",
        "您初步的研究问题或关注焦点是什么？",
        "有没有一件具体经历，或者一份读过的材料，让您开始关注这个主题？",
        "从这些线索中，您最想弄清楚的是什么？",
        "您对田野地点的确定程度如何？（已明确地点 / 有区域范围 / 希望我推荐）",
        "这次调研是单人还是团队？",
        "您预计调研多长时间？",
        "经费来源和大致预算范围是怎样的？",
        "希望在什么时间段进行调研？",
    ],
    PHASE_FIELD_SITE_PROFILES: [
        "请先告诉我您关注的目标区域或具体地点名称，我来帮您收集详细信息。",
        "除了主田野点，是否需要考察周边地区作为备选？",
        "您对住宿有什么特殊要求？（靠近调研点优先 / 经济型优先 / 网络条件要好）",
    ],
    PHASE_ITINERARY_PLANNING: [
        "您的出发城市是哪里？",
        "您需要往返票还是单程票？",
        "团队人数是多少？（影响机票总价估算）",
        "调研期间是否需要中途换点？",
    ],
    PHASE_PROPOSAL_GENERATION: [
        "研究标题希望怎么表述？我按八章结构起草，您可以逐章确认或改写。",
        "预算要不要给两档方案（节俭型/常规型）方便对比申报？",
        "风险评估与伦理预案里，您已经有哪些安排（知情同意、数据保密、进入许可）？",
        "参考文献需要我按学科惯例补上田野点的既有研究吗？请确认引用格式。",
    ],
    PHASE_FINISHED: [
        "方案已完成，想先逐段修改哪一章？",
        "需要我据此生成访谈提纲，或导出一个新版本吗？",
        "还有具体细节要调整吗（车次、住宿、缓冲日安排）？",
    ],
}

# 每阶段需要采集的 ResearchContext 字段名（供宿主渲染采集清单；
# 仅研究定位阶段的 ``check_advance`` 直接用它做推进计数）
REQUIRED_FIELDS: dict[str, list[str]] = {
    PHASE_RESEARCH_POSITIONING: ["topic", "research_questions"],
    PHASE_FIELD_SITE_PROFILES: ["target_location", "site_status"],
    PHASE_ITINERARY_PLANNING: ["departure_city", "target_location", "travel_date"],
    PHASE_PROPOSAL_GENERATION: ["topic", "target_location"],
    PHASE_FINISHED: [],
}


def phase_prompt(phase: str) -> str:
    """阶段系统提示词；未知阶段回落到研究定位（与旧 ``system_prompt`` 属性一致）。"""
    prompts = assets.system_prompts()
    return prompts.get(phase, prompts[PHASE_RESEARCH_POSITIONING])


def guiding_questions(phase: str) -> list[str]:
    """阶段引导问题模板；未知阶段回落到研究定位。返回副本，宿主改动不污染库。"""
    return list(GUIDING_QUESTIONS.get(phase, GUIDING_QUESTIONS[PHASE_RESEARCH_POSITIONING]))


def _next_phase(phase: str) -> str | None:
    """下一个阶段名；已是末阶段或未知阶段返回 ``None``。"""
    try:
        idx = PHASES.index(phase)
    except ValueError:
        return None
    if idx + 1 >= len(PHASES):
        return None
    return PHASES[idx + 1]


def _filled(ctx: ResearchContext, field: str) -> bool:
    """字段本体非空，或 ``extras`` 里有同名非空值即算已采集。"""
    value: Any = getattr(ctx, field, None) or ctx.extras.get(field)
    return bool(str(value).strip()) if isinstance(value, str) else bool(value)


def check_advance(
    phase: str,
    ctx: ResearchContext,
    sites: list[FieldSiteRecord],
    informants: list[InformantRecord],
    has_itinerary: bool,
    has_proposal: bool,
    proposal_confirmed: bool = False,
) -> AdvanceResult:
    """确定性判定能否推进（spec §2.1 + 审阅意见 R3 三态区分，纯函数无 I/O）。

    推进可基于"信息齐备/草稿已生成"（允许先探索）；但 D→finished 额外要求
    ``proposal_confirmed``——草稿存在不等于用户认可。``missing_fields`` 返回未满足项：
    A→B 为缺位字段名；B→C 为 ``confirmed_site`` / ``informant``（非访谈型研究可在
    ``ctx.interviews_not_applicable`` 下跳过后者）；C→D 为 ``itinerary``；
    D→finished 为 ``proposal`` / ``proposal_confirmation``。
    """
    revised = task_readiness(ctx.model_dump(), "field_site_comparison")
    if phase in PHASES[1:] and ctx.research_question_scope and not revised["ready"]:
        return AdvanceResult(can_advance=True, to_phase=PHASE_RESEARCH_POSITIONING,
                             missing_fields=revised["missing"])
    nxt = _next_phase(phase)
    if nxt is None:
        # 已完成或未知阶段：一律不推进
        return AdvanceResult(can_advance=False, to_phase=None, missing_fields=[])

    if phase == PHASE_RESEARCH_POSITIONING:
        missing = task_readiness(ctx.model_dump(), "field_site_comparison")["missing"]
        if not missing:
            return AdvanceResult(can_advance=True, to_phase=nxt, missing_fields=[])
        return AdvanceResult(can_advance=False, to_phase=None, missing_fields=missing)

    if phase == PHASE_FIELD_SITE_PROFILES:
        missing = []
        if not any(s.confirmed for s in sites):
            missing.append("confirmed_site")
        # 审阅意见 R3：非访谈型研究显式声明后跳过访谈对象硬条件
        if not informants and not ctx.interviews_not_applicable:
            missing.append("informant")
        if not missing:
            return AdvanceResult(can_advance=True, to_phase=nxt, missing_fields=[])
        return AdvanceResult(can_advance=False, to_phase=None, missing_fields=missing)

    if phase == PHASE_ITINERARY_PLANNING:
        if has_itinerary:
            return AdvanceResult(can_advance=True, to_phase=nxt, missing_fields=[])
        return AdvanceResult(can_advance=False, to_phase=None, missing_fields=["itinerary"])

    # PHASE_PROPOSAL_GENERATION（R3：草稿存在只算"已生成"，完成需用户确认）
    if not has_proposal:
        return AdvanceResult(can_advance=False, to_phase=None, missing_fields=["proposal"])
    if not proposal_confirmed:
        return AdvanceResult(can_advance=False, to_phase=None,
                             missing_fields=["proposal_confirmation"])
    return AdvanceResult(can_advance=True, to_phase=nxt, missing_fields=[])


_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def time_anchor(today: date | None = None) -> str:
    """当前日期锚点（system 最前段）：模型只有知道「今天」才不会给过去日期。"""
    day = today or date.today()
    return (f"今天是 {day.isoformat()}（{_WEEKDAYS[day.weekday()]}）。"
            "行程、机票/火车/酒店搜索一律从今天起算：出发日期与入住/离店日期必须是"
            "今天或之后的日期；计划里的历史日程不能当作实时搜索日期。过去日期会被"
            "拒绝并返回 flyai_invalid_date，请改用未来日期重试并向用户说明。")
