"""documents/proposal — 提案与访谈提纲文档生成（kernel 内唯一 LLM 消费者，spec 公理 2）。

迁移自旧 ``core/orchestrator.py`` 的 ``generate_full_proposal`` / ``_build_proposal_context``：
- 上下文由字符串拼接改为**结构化对象传入**（ctx / sites / itinerary / informants / knowledge）；
- LLM 经 keyword-only 参数注入（``llm=None`` 走模板降级路径），使 kernel 无运行时依赖；
- ``chat()`` 的 error 分支（``content`` 为空）同样降级到模板路径（controller 裁决），不抛异常；
- 八章标题与 ``phases.phase_prompt("proposal_generation")`` 一致（"一、…八、"中文编号），
  保持旧模板第四章 "详细行程安排" 的显式定位；
- ``mode`` 参数（global/kaiti/grant）：非 global 当前回退 global 提纲并追加"体例待深化"提示，
  依据 DEEP_RESEARCH 报告在后续任务 flesh out；
- ``section`` 非空 → 仅重写该章，其余段落保留（配合 ``prior_markdown``）。
"""
import re
from typing import Any

from fieldwork_agent.kernel import assets
from fieldwork_agent.kernel.guidance import informants, phases
from fieldwork_agent.kernel.models import (
    Envelope,
    ProposalDoc,
    ResearchContext,
    ToolError,
    compute_inputs_hash,
)
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from pydantic import BaseModel, Field, ValidationError

# 八章标题（与 phases.phase_prompt("proposal_generation") 内的模板一致；
# 迁移源：core/orchestrator.py `generate_full_proposal` 使用的 SYSTEM_PROMPTS[PROPOSAL]）
# 模板降级 (_degrade_markdown) 的章节标题也由此常量派生，做到"字面单一来源"
PROPOSAL_SECTIONS: list[str] = [
    "一、研究背景与问题提出",
    "二、田野点选择依据（含交通/住宿可行性分析）",
    "三、研究设计与方法论",
    "四、详细行程安排（含具体航班/车次/酒店信息）",
    "五、预算明细（基于实时搜索数据）",
    "六、风险评估与伦理预案",
    "七、预期成果",
    "八、参考文献",
]

# 能力边界（审阅意见 R1）：当前仅 global 完整实现；回退时在文档**头部前置声明**
# 而非仅尾注，避免用户误以为拿到的是目标体例的可直接提交材料。
SUPPORTED_MODES = ("global",)
MODE_FALLBACK_HEADER = ("> ⚠ 体例说明：您选择的「{mode}」体例尚未实现（开题/基金体例待深化），"
                        "本稿实际为通用八章草稿（适用海外/港澳申请与课程场景），"
                        "不构成可直接提交的申报材料。\n\n")

# 模板降级尾注（无 llm / llm error 分支共用）
TEMPLATE_DEGRADE_NOTE = "（模板降级版）"

# sections 抽取正则：仅 `## ` 开头的行计入（`# ` 与 `### ` 都不匹配）
_SECTION_RE = re.compile(r"^## .+", re.MULTILINE)


# ---------------------------------------------------------------- 渠道渲染


def _researcher_nonempty(ctx: ResearchContext) -> bool:
    """研究者信息渠道是否有内容：任一槽位非空或 extras 非空即算非空。"""
    if ctx.extras:
        return True
    fields = (ctx.discipline, ctx.topic, ctx.site_status, ctx.scale, ctx.budget,
              ctx.time_window, ctx.departure_city, ctx.target_location, ctx.travel_date)
    return any(str(v).strip() for v in fields)


def _bullets_researcher(ctx: ResearchContext) -> list[str]:
    lines: list[str] = []
    pairs = [
        ("学科", ctx.discipline),
        ("研究问题", ctx.topic),
        ("田野点确定度", ctx.site_status),
        ("调研规模", ctx.scale),
        ("预算", ctx.budget),
        ("时间窗口", ctx.time_window),
        ("出发城市", ctx.departure_city),
        ("目标地点", ctx.target_location),
        ("出行日期", ctx.travel_date),
    ]
    for k, v in pairs:
        if v:
            lines.append(f"- **{k}**：{v}")
    for k, v in ctx.extras.items():
        lines.append(f"- **{k}**：{v}")
    return lines or ["- （未填写研究者信息）"]


def _bullets_sites(sites: list[dict]) -> list[str]:
    lines: list[str] = []
    for s in sites:
        name = s.get("name", "未知")
        confirmed = "（已确认）" if s.get("confirmed") else ""
        lines.append(f"- **{name}**{confirmed}")
        for desc_key in ("description", "notes"):
            v = s.get(desc_key)
            if v:
                lines.append(f"  - {desc_key}：{v}")
        prof = s.get("profile")
        if isinstance(prof, dict):
            for k, v in prof.items():
                lines.append(f"  - {k}：{v}")
    return lines or ["- （无候选田野点）"]


def _bullets_itinerary(itinerary: dict | None) -> list[str]:
    if not itinerary:
        return ["- （暂无行程数据）"]
    lines: list[str] = []
    for k, v in itinerary.items():
        if k == "legs":
            legs = v if isinstance(v, list) else []
            if not legs:
                lines.append("- **legs**：[]")
            for leg in legs:
                lines.append(f"- 腿：{leg}")
        else:
            lines.append(f"- **{k}**：{v}")
    return lines


def _bullets_informants(items: list[dict]) -> list[str]:
    lines: list[str] = []
    for it in items:
        code = it.get("code", "")
        itype = it.get("type", "")
        head = f"- **{code or '未编号'}**"
        if itype:
            head += f"（{itype}）"
        lines.append(head)
        entry = informants.get_informant(itype) if itype else None
        if entry:
            lines.append(f"  - 展示名：{entry.get('name', itype)}")
            lines.append(f"  - 接近策略：{entry['approach']}")
            lines.append("  - 提问域：" + "、".join(entry["question_domains"]))
            lines.append(f"  - 注意事项：{entry['cautions']}")
            lines.append(f"  - 伦理：{entry['ethics']}")
        if it.get("relation"):
            lines.append(f"  - 关系：{it['relation']}")
        if it.get("notes"):
            lines.append(f"  - 备注：{it['notes']}")
    return lines or ["- （暂无访谈对象）"]


def _bullets_knowledge(items: list[dict]) -> list[str]:
    lines: list[str] = []
    for k in items:
        title = k.get("title", "")
        source = k.get("source")
        suffix = f"（{source}）" if source else ""
        lines.append(f"- {title}{suffix}")
    return lines


# ---------------------------------------------------------------- 上下文组装


def build_context_block(ctx: ResearchContext, sites: list[dict], itinerary: dict | None,
                        informants: list[dict], knowledge: list[dict]) -> str:
    """五渠道 markdown；四个硬渠道任一为空 raise ``ValueError("缺少渠道: <名>")``。

    知识库是软渠道（spec L1 断言只覆盖研究者/田野点/行程/访谈对象）：
    空则整段跳过、有则拼为最后一节。参数名 ``informants`` 与 brief 接口一致；
    本函数不直接引用 ``kernel.guidance.informants`` 模块（渲染交由 _bullets_* 助手），故遮蔽无害。
    """
    if not _researcher_nonempty(ctx):
        raise ValueError("缺少渠道: 研究者信息")
    if not sites:
        raise ValueError("缺少渠道: 田野点")
    if itinerary is None or (isinstance(itinerary, dict) and len(itinerary) == 0):
        raise ValueError("缺少渠道: 行程")
    if not informants:
        raise ValueError("缺少渠道: 访谈对象")

    def _section(title: str, bullets: list[str]) -> str:
        return "\n".join([f"## {title}", "", *bullets])

    blocks = [
        _section("研究者信息", _bullets_researcher(ctx)),
        _section("田野点", _bullets_sites(sites)),
        _section("行程", _bullets_itinerary(itinerary)),
        _section("访谈对象", _bullets_informants(informants)),
    ]
    kb = _bullets_knowledge(knowledge)
    if kb:
        blocks.append(_section("知识库", kb))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- 模板降级


def _degrade_markdown(ctx: ResearchContext, sites: list[dict], itinerary: dict | None,
                      informant_items: list[dict], knowledge: list[dict]) -> str:
    """无 llm（或 llm 返回错误分支）时的八章模板拼接：渠道数据直嵌 + 尾注。

    章节标题一律派生自 ``PROPOSAL_SECTIONS``（与 phases.phase_prompt 的字面同源），
    章内正文按下标一一对应。结构上严格保证只出现 8 行 ``^## ``（渠道 bullets
    一律 `- `/`  - ` 起头），使 ``sections`` 抽取结果稳定 8 项——这是 brief Step 1
    断言 `len(sections) == 8` 的前提。
    """
    title = ctx.topic or ctx.target_location or "未命名"
    lines: list[str] = [f"# 研究方案：{title}", ""]

    bodies: list[list[str]] = [
        _bullets_researcher(ctx),
        _bullets_sites(sites),
        _bullets_informants(informant_items),
        _bullets_itinerary(itinerary),
        [
            f"- 预算：{ctx.budget or '待补充'}",
            "- 数据来源：本方案为模板降级版，未接入 LLM 与飞猪实价，请宿主补录",
        ],
        [
            f"- 时间窗口：{ctx.time_window or '待确认'}",
            "- 伦理安排：详见访谈提纲的知情同意话术条目（compose_interview_guide 产出）",
        ],
        ["- 待研究者补充（模板降级版不臆测成果形态）"],
        _bullets_knowledge(knowledge) or ["- 待补检索清单（知识库渠道为空：请补充文献后再完善，"
                                          "本稿不虚构引用）"],
    ]
    # strict=True：PROPOSAL_SECTIONS 与 bodies 若长度漂移，立即报错而不是静默丢章
    for heading, body in zip(PROPOSAL_SECTIONS, bodies, strict=True):
        lines += [f"## {heading}", *body, ""]
    lines.append(TEMPLATE_DEGRADE_NOTE)
    return "\n".join(lines)


# ---------------------------------------------------------------- LLM 调用


def _llm_full_proposal(context_block: str, llm: Any) -> str:
    """全量生成：system=phase_prompt("proposal_generation") + user=context_block+固定尾句。

    尾句含文献红线（审阅意见 R1）。``chat()`` 的 error 分支（content 空串）返回空串，
    由调用方降级到模板路径。
    """
    messages = [
        {"role": "system", "content": phases.phase_prompt("proposal_generation")},
        {"role": "user", "content": f"{context_block}\n\n请生成完整研究方案。"
         "参考文献只能使用上下文提供的资料条目，缺材料时在第八章列出待补检索清单，"
         "不得虚构或补造引用。"},
    ]
    resp = llm.chat(messages, slot="proposal", max_tokens=8192, temperature=0.5)
    return (resp or {}).get("content", "") or ""


def _splice_section(prior: str, section: str, new_block: str) -> str:
    """把 prior 中 ``## <section>`` 到下一个 ``^## `` 之前的整块替换为 new_block。

    找不到目标章节时在 prior 尾部追加（保底不吞内容），确保重写不因锚点失误而丢数据。
    """
    pattern = re.compile(
        rf"^## {re.escape(section)}.*?(?=^## |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    replaced = new_block.rstrip() + "\n\n"
    if pattern.search(prior):
        return pattern.sub(replaced, prior, count=1)
    return prior.rstrip() + "\n\n" + replaced


def _llm_rewrite_section(context_block: str, section: str, prior_markdown: str,
                         llm: Any) -> str:
    """单章重写：LLM 只输出目标 ``## <section>`` 一节，再 splice 回 prior_markdown。"""
    user_prompt = (
        f"{context_block}\n\n"
        f"现有研究方案：\n{prior_markdown}\n\n"
        f"请只重写章节：## {section}"
        f"（输出以 `## {section}` 起始、仅返回该章节本身，不要重复其他章节）"
    )
    messages = [
        {"role": "system", "content": phases.phase_prompt("proposal_generation")},
        {"role": "user", "content": user_prompt},
    ]
    resp = llm.chat(messages, slot="proposal", max_tokens=8192, temperature=0.5)
    content = (resp or {}).get("content", "") or ""
    if not content:
        return ""
    if not content.lstrip().startswith(f"## {section}"):
        content = f"## {section}\n{content.strip()}"
    return _splice_section(prior_markdown, section, content)


# ---------------------------------------------------------------- 能力实现


def compose_proposal(ctx: ResearchContext, sites: list[dict], itinerary: dict | None,
                     informants: list[dict], knowledge: list[dict], *,
                     llm: Any = None, section: str | None = None, mode: str = "global",
                     prior_markdown: str = "") -> ProposalDoc:
    """按结构化上下文生成研究方案 ``ProposalDoc``（无 llm 时模板降级，非 global 追加提示）。

    ``llm`` 走 keyword-only 注入（spec 公理 2），无 key 环境传 None；
    参数 ``informants`` 与 brief 接口签名一致，本函数不直接引用同名模块（仅位置传参）。
    """
    context_block = build_context_block(ctx, sites, itinerary, informants, knowledge)

    if section and llm is not None and prior_markdown:
        markdown = (_llm_rewrite_section(context_block, section, prior_markdown, llm)
                    or _degrade_markdown(ctx, sites, itinerary, informants, knowledge))
    elif llm is None:
        markdown = _degrade_markdown(ctx, sites, itinerary, informants, knowledge)
    else:
        markdown = (_llm_full_proposal(context_block, llm)
                    or _degrade_markdown(ctx, sites, itinerary, informants, knowledge))

    requested = mode if mode in ("global", "kaiti", "grant") else "global"
    effective = requested if requested in SUPPORTED_MODES else "global"
    if effective != requested:
        # 审阅意见 R1：能力限制头部前置声明（而非仅在文末附注）
        markdown = MODE_FALLBACK_HEADER.format(mode=requested) + markdown

    return ProposalDoc(
        markdown=markdown,
        sections=_SECTION_RE.findall(markdown),
        requested_mode=requested,
        mode=effective,
        inputs_hash=compute_inputs_hash({"context_block": context_block}),
    )


def _entry_block(entry: dict | None, itype: str = "") -> str:
    if not entry:
        return "（该类型未收录于访谈对象库；请按通用半结构式框架起草）"
    parts = [
        f"展示名：{entry.get('name', itype)}",
        f"接近策略：{entry['approach']}",
        "建议提问域：" + "、".join(entry["question_domains"]),
        f"注意事项：{entry['cautions']}",
        f"伦理提示：{entry['ethics']}",
    ]
    return "\n".join(parts)


def _guide_degrade(topic: str, itype: str) -> str:
    """无 llm 时的模板提纲：条目直嵌 + 方法论全文（保证含"提问域"与"知情同意"字样）。"""
    entry = informants.get_informant(itype)
    head = f"# 访谈提纲：{topic} × {itype}（模板降级版）"
    if not entry:
        return f"{head}\n\n{informants.INTERVIEW_GUIDE_METHODOLOGY}"
    body = "\n".join([
        f"展示名：{entry.get('name', itype)}",
        "",
        "## 接近策略",
        entry["approach"],
        "",
        "## 提问域",
        *[f"- {d}" for d in entry["question_domains"]],
        "",
        "## 注意事项",
        entry["cautions"],
        "",
        "## 伦理与知情同意",
        entry["ethics"],
        "",
        informants.INTERVIEW_GUIDE_METHODOLOGY,
    ])
    return f"{head}\n\n{body}"


def _guide_prompt(topic: str, itype: str) -> str:
    """guide 的 user prompt：固定包含 INTERVIEW_GUIDE_METHODOLOGY 与"知情同意话术"字样。"""
    entry = informants.get_informant(itype)
    return (
        f"研究主题：{topic}\n\n"
        f"目标报道人类型：{itype}\n\n"
        f"该类型条目：\n{_entry_block(entry, itype)}\n\n"
        f"{informants.INTERVIEW_GUIDE_METHODOLOGY}\n\n"
        + assets.generation_prompt("guide_instruction")
    )


def compose_interview_guide(topic: str, informant_types: list[str], *,
                            llm: Any = None) -> dict[str, str]:
    """每类报道人产出一份半结构式提纲；无 llm 或 llm error 分支都走模板降级。"""
    guides: dict[str, str] = {}
    for itype in informant_types:
        content = ""
        if llm is not None:
            messages = [
                {"role": "system", "content": assets.generation_prompt("guide_system")},
                {"role": "user", "content": _guide_prompt(topic, itype)},
            ]
            resp = llm.chat(messages, slot="interview", max_tokens=4096, temperature=0.5)
            content = (resp or {}).get("content", "") or ""
        guides[itype] = content or _guide_degrade(topic, itype)
    return guides


# ---------------------------------------------------------------- 能力注册
#
# 两个 handler 是 **registry 侧的分派入口**（``CapabilityMeta.fn``），与搜索原语/聚合
# 能力同构：接 JSON 友好的入参、``llm`` 为宿主注入的 keyword-only 参数（不进 schema）、
# 一律返回 ``Envelope``（spec §2.4 统一包络）。为此它们负责把宿主 JSON 直传的 ``ctx``
# ``model_validate`` 为 ``ResearchContext``（否则底层按对象访问 ``ctx.extras`` 会炸），
# 并把 ``build_context_block`` 的缺渠道 ValueError 折成 ok=False 包络。
#
# 例外：宿主 ``FieldAgent.generate_proposal`` 是"命令式直调"而非工具分派，继续直调底层
# ``compose_proposal``，其 ValueError→{ok:False} 语义与既有测试逐字不动。


class ComposeProposalArgs(BaseModel):
    """``compose_proposal`` 的入参模型（``llm`` 注入参数不进 schema）。"""

    ctx: dict[str, Any] = Field(default_factory=dict)
    sites: list[dict[str, Any]] = Field(default_factory=list)
    itinerary: dict[str, Any] | None = None
    informants: list[dict[str, Any]] = Field(default_factory=list)
    knowledge: list[dict[str, Any]] = Field(default_factory=list)
    section: str | None = None
    mode: str = "global"
    prior_markdown: str = ""


class ComposeInterviewGuideArgs(BaseModel):
    """``compose_interview_guide`` 的入参模型（``llm`` 注入参数不进 schema）。"""

    topic: str
    informant_types: list[str] = Field(default_factory=list)


def proposal_handler(ctx: dict, sites: list, itinerary: dict | None,
                     informants: list, knowledge: list, *, llm: Any = None,
                     section: str | None = None, mode: str = "global",
                     prior_markdown: str = "") -> Envelope:
    """registry 分派入口：宿主以 JSON 传 ``ctx`` dict，内核转 ResearchContext 后统一包络。

    终审 I2（验收③）：``llm is None`` 且 compose 成功时，包络带非空 ``degraded``，
    使"模板降级"对机器可见（此前只在 markdown 尾注里，人眼可见、程序不可见）。
    """
    try:
        doc = compose_proposal(
            ResearchContext.model_validate(ctx or {}), sites or [], itinerary,
            informants or [], knowledge or [],
            llm=llm, section=section, mode=mode, prior_markdown=prior_markdown,
        )
    except ValidationError as exc:  # ctx 形状不合 ResearchContext（先于 ValueError 捕获）
        return Envelope(ok=False, error=ToolError(
            code="invalid_arguments", message=f"ctx 校验失败：{exc}",
            hint="ctx 需为 ResearchContext 槽位形状的 dict"))
    except ValueError as exc:
        # build_context_block 的四个硬渠道缺失（消息形如 "缺少渠道: 行程"）
        return Envelope(ok=False, error=ToolError(
            code="missing_channel", message=str(exc),
            hint="先补齐研究者信息/田野点/行程/访谈对象四渠道，再生成方案"))
    if llm is None:
        # 措辞对齐 informants.suggest_informants 的既有 degraded 风格（"参考起点，非完整覆盖"）；
        # llm 非 None 路径不动（chat error 分支的内部降级仍由 compose_proposal 尾注承担）
        return Envelope(ok=True, data=doc, degraded="模板降级版（未接入 LLM）")
    return Envelope(ok=True, data=doc)


def guide_handler(topic: str, informant_types: list, *, llm: Any = None) -> Envelope:
    """registry 分派入口：逐类提纲折进 ``Envelope.data``（``dict[str, str]``）。"""
    return Envelope(ok=True,
                    data=compose_interview_guide(topic, informant_types or [], llm=llm))


register(CapabilityMeta(
    name="compose_proposal",
    description="按结构化上下文（研究者/田野点/行程/访谈对象/知识库五渠道）生成研究方案草稿；"
                "documents 是 kernel 内唯一 LLM 消费模块，无 key 时模板降级、chat error 分支同样降级；"
                "mode 枚举 global|kaiti|grant，requested/actual 分开记录，不支持的体例在头部前置声明限制；"
                "参考文献仅限所给条目不得虚构；section 非空则仅重写该章。",
    fn=proposal_handler,
    args_model=ComposeProposalArgs,
))

register(CapabilityMeta(
    name="compose_interview_guide",
    description="为每类报道人（INFORMANT_LIBRARY 键）生成半结构式访谈提纲；"
                "user prompt 固定包含 INTERVIEW_GUIDE_METHODOLOGY 与知情同意话术字样，"
                "无 llm 时走模板降级、直嵌条目字段与方法论全文。",
    fn=guide_handler,
    args_model=ComposeInterviewGuideArgs,
))
