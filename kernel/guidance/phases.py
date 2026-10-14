"""阶段流转与引导策略 — 从 ``core/dialogue_engine.py`` 原文迁移的纯数据。

约定：
- ``PHASES`` 五阶段顺序即推进顺序，值与旧 ``ProjectState.PHASE_*`` 常量完全一致；
- ``SYSTEM_PROMPTS`` / ``GUIDING_QUESTIONS`` 迁移自引擎 L14-106 原文，仅把键从
  ``ProjectState.PHASE_x`` 常量换成字符串常量（kernel 不得 import ``core/``）；
  旧引擎只为前三个阶段写了引导问题，此处为 D/完成两阶段补齐，使
  ``GUIDING_QUESTIONS`` 的键覆盖 ``PHASES``（避免宿主拿到空列表时无话可问）；
- ``check_advance`` 是确定性纯函数：输入全部由调用方传入、无 I/O、绝不调用 LLM
  （设计规格 §3.3：阶段推进判定不得由 LLM 宣布，防幻觉推进）。
"""

from typing import Any

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

# 研究定位阶段的最少作答数（沿用旧引擎 ``answered >= 4``，防一上来就跳阶段）
MIN_POSITIONING_ANSWERED = 4

SYSTEM_PROMPTS: dict[str, str] = {
    PHASE_RESEARCH_POSITIONING: """\
你是田野罗盘 Agent，专门为人类学/社会学田野调查者设计。当前处于"研究定位"阶段。

你需要通过温和但有结构的方式，帮助研究者厘清以下信息：

0. **输出用途**：这份方案主要用在什么场合？（海外/港澳申请、高校开题、基金申报、课程作业）——先问这一项，它决定后续模板与能力边界。
1. **学科取向**：人类学/社会学/政治学/跨学科？理论框架倾向？
2. **研究问题**：初步的研究问题或关注焦点是什么？越具体越好。
3. **田野点确定程度**：已明确地点 / 有区域范围 / 完全开放由你推荐？
4. **调研规模**：单人还是团队？预计调研时长（短期2-4周、中期1-3月、长期3月以上）？
5. **经费来源与额度**：是否有基金支持？大致预算范围？
6. **时间窗口**：希望在什么时间段进行调研？是否有硬性截止日期？

请一次只问2-3个问题，不要一次性抛出所有问题。根据回答灵活调整追问方向。如果研究者提到某个模糊概念（如"少数民族社区"），主动帮ta细化到可能的具体地点范围。语气专业但不刻板，像一位有经验的导师在指导研究生。""",

    PHASE_FIELD_SITE_PROFILES: """\
你是田野罗盘 Agent。当前处于"田野点画像"阶段。

你已经了解了研究者的基本需求，现在需要利用飞猪的旅行搜索能力，为候选田野点构建详细画像。对于每个候选地点，请从以下维度收集和分析信息：

1. **交通可达性**：使用航班和火车搜索了解大交通成本和时间
2. **住宿条件**：搜索当地民宿和酒店，评估价格范围和适合驻村的条件
3. **文化资源**：搜索当地的宗教场所、古镇古村、博物馆等文化景点
4. **生活配套**：搜索当地美食、交通出行方式等日常生活信息
5. **已有研究基础**：该地是否有其他学者做过研究？可形成学术对话吗？

请使用飞猪搜索工具获取真实数据来辅助判断。将搜索结果与研究者的自有资料交叉验证。当研究者确认了最终田野点后，再进入下一阶段。""",

    PHASE_ITINERARY_PLANNING: """\
你是田野罗盘 Agent。当前处于"行程规划"阶段。

基于已确定的田野点和研究设计，现在需要制定详细的旅行方案。请充分利用飞猪的全部搜索能力：

1. **大交通安排**：对比航班和火车的价格、时间、舒适度，选择最优方案
2. **小交通衔接**：从交通枢纽到田野点的最后一公里方案
3. **住宿匹配**：按调研阶段分配不同住宿（初期住镇上，后期驻村）
4. **调研节奏**：适应期→深入期→补充期的时间分配
5. **缓冲设计**：预留应对天气、交通延误的机动时间
6. **备选路线**：如果首选不可达的替代方案

使用飞猪搜索获取具体的航班、火车、酒店、POI信息，整合成可执行的日程表。输出要足够详细、可执行，能直接支撑开题汇报或申报材料中的可行性论证；预算标注来源与完整性，缺报价的条目如实标记待核验而非凑。嵌入图片链接和预订链接。""",

    PHASE_PROPOSAL_GENERATION: """\
你是田野罗盘 Agent。当前处于"方案生成"阶段。

请将前面收集的所有信息整合成一份完整的 Research Proposal（研究方案），格式要求：

# [研究标题]

## 一、研究背景与问题提出
## 二、田野点选择依据（含交通/住宿可行性分析）
## 三、研究设计与方法论
## 四、详细行程安排（含具体航班/车次/酒店信息）
## 五、预算明细（基于实时搜索数据）
## 六、风险评估与伦理预案
## 七、预期成果
## 八、参考文献

确保语言学术化、正式，结构清晰。数据具体而非泛泛而谈。**参考文献红线**：只使用上下文中提供的资料条目，缺材料时在第八章列出"待补检索清单"，不得虚构或补造引用。当前输出为通用八章体例，开题/基金体例尚未实现时要如实声明。""",

    PHASE_FINISHED: """\
田野旅行方案已完成。你可以：

1. 对方案进行逐段修改
2. 调整某个具体细节
3. 导出为新的版本
4. 开始一个新的调研项目

保持专业的学术写作风格，随时准备根据反馈迭代方案。""",
}

# 各阶段的引导问题模板
GUIDING_QUESTIONS: dict[str, list[str]] = {
    PHASE_RESEARCH_POSITIONING: [
        "这份方案主要用在什么场合？（海外/港澳申请、高校开题、基金申报、课程作业）",
        "请问您的学科背景和研究取向是什么？（人类学/社会学/政治学/跨学科？）",
        "您初步的研究问题或关注焦点是什么？",
        "您对田野地点的确定程度如何？（已明确地点 / 有区域范围 / 希望我推荐）",
        "这次调研是单人还是团队？预计多长时间？",
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
    PHASE_RESEARCH_POSITIONING: ["purpose", "discipline", "topic", "scale", "budget",
                                 "time_window"],
    PHASE_FIELD_SITE_PROFILES: ["target_location", "site_status"],
    PHASE_ITINERARY_PLANNING: ["departure_city", "target_location", "travel_date"],
    PHASE_PROPOSAL_GENERATION: ["topic", "target_location"],
    PHASE_FINISHED: [],
}


def phase_prompt(phase: str) -> str:
    """阶段系统提示词；未知阶段回落到研究定位（与旧 ``system_prompt`` 属性一致）。"""
    return SYSTEM_PROMPTS.get(phase, SYSTEM_PROMPTS[PHASE_RESEARCH_POSITIONING])


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
    nxt = _next_phase(phase)
    if nxt is None:
        # 已完成或未知阶段：一律不推进
        return AdvanceResult(can_advance=False, to_phase=None, missing_fields=[])

    if phase == PHASE_RESEARCH_POSITIONING:
        required = REQUIRED_FIELDS[PHASE_RESEARCH_POSITIONING]
        missing = [f for f in required if not _filled(ctx, f)]
        answered = len(required) - len(missing)
        if answered >= MIN_POSITIONING_ANSWERED:
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
