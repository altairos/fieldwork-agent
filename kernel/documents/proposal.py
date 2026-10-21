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
from fieldwork_agent.kernel.llm import is_input_too_long
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
# 体例枚举 → 中文标签（与前端 ProposalCard MODE_LABEL 同口径）：回退头部插入中文标签，
# 避免导出文档头部出现裸英文枚举。
MODE_LABELS = {"global": "英美式八章", "kaiti": "开题报告", "grant": "基金申报"}
MODE_FALLBACK_HEADER = ("> ⚠ 体例说明：您选择的「{mode}」体例尚未实现（开题/基金体例待深化），"
                        "本稿实际为英美式八章草稿（适用海外/港澳申请与课程场景），"
                        "不构成可直接提交的申报材料。\n\n")

# 模板降级尾注（无 llm / llm error 分支共用）
TEMPLATE_DEGRADE_NOTE = "（模板降级版）"

# sections 抽取正则：仅 `## ` 开头的行计入（`# ` 与 `### ` 都不匹配）
_SECTION_RE = re.compile(r"^## .+", re.MULTILINE)

# P1-11：上下文预算。site profile / 行程里嵌套的商品明细是 JSON 直嵌，多轮会话后
# 会把 prompt 顶到模型输入上限（实测连续两次 input too long）。两道闸：
#   1) 单个值先压缩（只保留名称/价格/距离/时长这类结论字段）；
#   2) 每个渠道再给字符预算，超出部分整行截断并写明省略行数——绝不静默丢数据。
_VALUE_CHAR_LIMIT = 200
_SECTION_CHAR_BUDGET = {
    "研究者信息": 3000,
    "田野点": 8000,
    "行程": 8000,
    "访谈对象": 4000,
    "知识库": 4000,
}
# 单个值的一级键上限（嵌套 dict/list 只取前几个键，其余折叠为计数）
_VALUE_ITEM_LIMIT = 6


def _compact_value(value: Any, limit: int = _VALUE_CHAR_LIMIT) -> str:
    """把任意渠道值压成一行可读文本（P1-11 预算闸门）。

    dict/list 不在 prompt 里整段 JSON 直嵌：只保留一级键与前若干项，超出部分
    以“…（共 N 项）”标注；字符超限再尾部截断。既保住结论字段（价格/距离/
    名称），又不会让嵌套明细把上下文吃光。
    """
    if isinstance(value, dict):
        parts: list[str] = []
        for i, (k, v) in enumerate(value.items()):
            if i >= _VALUE_ITEM_LIMIT:
                parts.append(f"…（共 {len(value)} 个键）")
                break
            parts.append(f"{k}={_compact_value(v, limit)}")
        text = "，".join(parts)
    elif isinstance(value, (list, tuple)):
        parts = [
            _compact_value(v, limit) for v in list(value)[:_VALUE_ITEM_LIMIT]]
        text = "；".join(parts)
        if len(value) > _VALUE_ITEM_LIMIT:
            text += f"；…（共 {len(value)} 项）"
    else:
        text = str(value)
    text = " ".join(text.split())   # 压掉换行/重复空白，避免一条 bullet 分多行
    return text if len(text) <= limit else text[:limit] + "…"


def _clip_lines(lines: list[str], budget: int) -> list[str]:
    """按字符预算截断一个渠道的 bullets，并显式标注被省略的行数（P1-11）。"""
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if used + cost > budget:
            kept.append(f"- （其余 {len(lines) - len(kept)} 行因上下文预算省略）")
            return kept
        kept.append(line)
        used += cost
    return kept


# ---------------------------------------------------------------- 渠道渲染


def _researcher_nonempty(ctx: ResearchContext) -> bool:
    """研究者信息渠道是否有内容：任一槽位非空或 extras 非空即算非空。"""
    if ctx.extras:
        return True
    fields = (ctx.discipline, ctx.topic, ctx.site_status, ctx.scale, ctx.budget,
              ctx.time_window, ctx.departure_city, ctx.target_location, ctx.travel_date,
              ctx.purpose, ctx.research_questions, ctx.research_orientation,
              ctx.target_institution, ctx.target_department, ctx.target_degree, ctx.target_advisor)
    return any(str(v).strip() for v in fields)


def _bullets_researcher(ctx: ResearchContext) -> list[str]:
    lines: list[str] = []
    pairs = [
        ("学科", ctx.discipline),
        ("研究主题", ctx.topic),
        ("研究问题", ctx.research_questions),
        ("学术取向", ctx.research_orientation),
        ("目标院校", ctx.target_institution),
        ("目标院系", ctx.target_department),
        ("目标学位", ctx.target_degree),
        ("目标导师", ctx.target_advisor),
        ("输出用途", ctx.purpose),
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
                lines.append(f"  - {desc_key}：{_compact_value(v)}")
        prof = s.get("profile")
        if isinstance(prof, dict):
            for k, v in prof.items():
                lines.append(f"  - {k}：{_compact_value(v)}")
        elif prof:
            lines.append(f"  - profile：{_compact_value(prof)}")
    return lines or ["- （无候选田野点）"]


_DIRECTION_ZH = {"outbound": "去程", "inter": "站间", "return": "返程"}


def _booking_suffix(chosen: dict) -> str:
    """chosen 带飞猪 ``jump_url`` 时尾接 markdown 预订链接（A3/D11），否则空串。"""
    jump = chosen.get("jump_url")
    if not isinstance(jump, str) or not jump.startswith(("http://", "https://")):
        return ""
    return f" [预订]({jump})"


def _fmt_price(value) -> str:
    """价格 → ``¥350``（整数抹 .0）/ ``¥150.5`` 文本；非数字原样 str；大额不落科学计数法。"""
    if isinstance(value, int):
        return f"¥{value}"
    if isinstance(value, float):
        return f"¥{format(value, 'f').rstrip('0').rstrip('.')}"
    return str(value)


def _leg_line(leg: dict) -> str:
    chosen = leg.get("chosen")
    route = f"{leg.get('from', '?')} → {leg.get('to', '?')}"
    date = leg.get("date", "?")
    if not isinstance(chosen, dict):
        return f"- 腿：{route}（{date}）：无可用价格，预算未计"
    direction = _DIRECTION_ZH.get(str(leg.get("direction", "")))
    head = f"- 腿：{route}（{date}，{direction}）" if direction else f"- 腿：{route}（{date}）"
    n_alts = len(leg.get("alternatives") or [])
    alts = f"，备选 {n_alts} 条" if n_alts else ""
    return f"{head}：{chosen.get('name', '未命名')} {_fmt_price(chosen.get('price'))}{alts}{_booking_suffix(chosen)}"


def _stay_line(stay: dict) -> str:
    chosen = stay.get("chosen")
    head = f"- 住宿（{stay.get('dest', '?')}）{stay.get('check_in', '?')} → {stay.get('check_out', '?')}"
    nights = stay.get("nights")
    if nights is not None:
        head += f" · {nights} 晚"
    if not isinstance(chosen, dict):
        return f"{head}：无可用价格"
    nightly = stay.get("nightly_price")
    nightly_txt = f" {_fmt_price(nightly)}/晚" if isinstance(nightly, (int, float)) else ""
    return f"{head}：{chosen.get('name', '未命名')}{nightly_txt}{_booking_suffix(chosen)}"


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
                lines.append(_leg_line(leg if isinstance(leg, dict) else {}))
        elif k == "stays":
            stays = v if isinstance(v, list) else []
            if not stays:
                lines.append("- **stays**：[]")
            for stay in stays:
                lines.append(_stay_line(stay if isinstance(stay, dict) else {}))
        else:
            lines.append(f"- **{k}**：{_compact_value(v, 600)}")
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


def _informants_bullets(items: list[dict], ctx: ResearchContext) -> list[str]:
    """访谈对象 bullets（审计 F12）：非访谈研究豁免空候选，其余沿用既有渲染。"""
    if items:
        return _bullets_informants(items)
    if getattr(ctx, "interviews_not_applicable", False):
        reason = getattr(ctx, "interviews_na_reason", "") or "研究者已声明本研究不适用访谈方法"
        return [f"- 不适用访谈方法：{reason}"]
    return _bullets_informants(items)  # 空且未声明 NA → 既有占位（raise 由调用方把关）


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
    if not informants and not getattr(ctx, "interviews_not_applicable", False):
        raise ValueError("缺少渠道: 访谈对象")

    def _section(title: str, bullets: list[str]) -> str:
        kept = _clip_lines(bullets, _SECTION_CHAR_BUDGET.get(title, 4000))
        return "\n".join([f"## {title}", "", *kept])

    blocks = [
        _section("研究者信息", _bullets_researcher(ctx)),
        _section("田野点", _bullets_sites(sites)),
        _section("行程", _bullets_itinerary(itinerary)),
        _section("访谈对象", _informants_bullets(informants, ctx)),
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
        _informants_bullets(informant_items, ctx),
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


def _llm_failure_reason(error: object) -> str:
    """LLM 失败原因 → 机器可读前缀 + 原文（P1-11）。

    输入超长不能与“网络/额度失败”混为一谈：前者重试无用、需要缩提示或换模型，
    且不应被当成“模型暂时不可用”。命中时前缀写成 ``input_too_long``，
    让宿主/UI/自动化能直接判定。
    """
    text = str(error or "").strip()
    if is_input_too_long(text):
        return f"input_too_long: {text}" if text else "input_too_long"
    return f"调用异常: {text}" if text else "空响应"


def _llm_full_proposal(context_block: str, llm: Any) -> tuple[str, str | None]:
    """全量生成：system=phase_prompt("proposal_generation") + user=context_block+固定尾句。

    尾句含文献红线（审阅意见 R1）。返回 ``(content, error_reason)``（P0-1）：
    ``chat()`` 的 error 分支（content 空串）或抛异常时 content 为空、reason 说明
    失败原因，由 ``compose_proposal`` 回落到模板并把原因写进 ``degraded``——
    绝不再静默产出“可当成品”的模板正文。
    """
    messages = [
        {"role": "system", "content": phases.phase_prompt("proposal_generation")},
        {"role": "user", "content": f"{context_block}\n\n请生成完整研究方案。"
         "参考文献只能使用上下文提供的资料条目，缺材料时在第八章列出待补检索清单，"
         "不得虚构或补造引用。"},
    ]
    try:
        resp = llm.chat(messages, slot="proposal", max_tokens=8192, temperature=0.5)
    except Exception as exc:  # noqa: BLE001 —— 回落到模板并把异常原因如实上报
        return "", _llm_failure_reason(exc)
    content = (resp or {}).get("content", "") or ""
    if not content:
        upstream = (resp or {}).get("error")
        return "", _llm_failure_reason(upstream) if upstream else "空响应"
    return content, None


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
                         llm: Any) -> tuple[str, str | None]:
    """单章重写：LLM 只输出目标 ``## <section>`` 一节，再 splice 回 prior_markdown。

    返回 ``(markdown, error_reason)``（P0-1）：失败时 markdown 为空，调用方保留
    原方案并标记 degraded（而不是把整篇模板当成“重写结果”静默返回）。
    """
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
    try:
        resp = llm.chat(messages, slot="proposal", max_tokens=8192, temperature=0.5)
    except Exception as exc:  # noqa: BLE001
        return "", _llm_failure_reason(exc)
    content = (resp or {}).get("content", "") or ""
    if not content:
        upstream = (resp or {}).get("error")
        return "", _llm_failure_reason(upstream) if upstream else "空响应"
    if not content.lstrip().startswith(f"## {section}"):
        content = f"## {section}\n{content.strip()}"
    return _splice_section(prior_markdown, section, content), None


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

    # P0-1：LLM 不可用/调用失败/空响应都会回落模板，但必须留下机器可见的
    # degraded 标记（此前只在正文尾注里，包络与 data 均无信号）。
    degraded: str | None = None
    if section and llm is not None and prior_markdown:
        markdown, reason = _llm_rewrite_section(context_block, section, prior_markdown, llm)
        if not markdown:
            # P3-3：单章重写失败不再整篇回落模板——保留 prior_markdown，
            # 只标记目标章节未更新，避免“改一章、丢全文”。
            degraded = (f"单章未更新（LLM 调用失败：{reason}）："
                        f"已保留原方案，仅「{section}」未重写")
            markdown = prior_markdown
    elif llm is None:
        degraded = "模板降级版（未接入 LLM）"
        markdown = _degrade_markdown(ctx, sites, itinerary, informants, knowledge)
    else:
        markdown, reason = _llm_full_proposal(context_block, llm)
        if not markdown:
            degraded = f"模板降级版（LLM 调用失败：{reason}）"
            markdown = _degrade_markdown(ctx, sites, itinerary, informants, knowledge)

    requested = mode if mode in ("global", "kaiti", "grant") else "global"
    effective = requested if requested in SUPPORTED_MODES else "global"
    if effective != requested:
        # 审阅意见 R1：能力限制头部前置声明（而非仅在文末附注）。
        # P1-11：同时写进 degraded——否则模型看不到“体例没实现”，会在正文里
        # 自由发挥成致歉说明；带上标记后只需一句如实说明。
        markdown = MODE_FALLBACK_HEADER.format(mode=MODE_LABELS.get(requested, requested)) + markdown
        note = (f"体例降级：请求「{MODE_LABELS.get(requested, requested)}」，"
                f"实际产出「{MODE_LABELS.get(effective, effective)}」草稿")
        degraded = f"{degraded}；{note}" if degraded else note

    return ProposalDoc(
        markdown=markdown,
        sections=_SECTION_RE.findall(markdown),
        requested_mode=requested,
        mode=effective,
        inputs_hash=compute_inputs_hash({"context_block": context_block}),
        degraded=degraded,
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
    head = f"# 访谈提纲：{topic} × {itype}{TEMPLATE_DEGRADE_NOTE}"
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


def _guide_prompt(topic: str, itype: str, *,
                  research_questions: str = "", discipline: str = "") -> str:
    """guide 的 user prompt：固定包含 INTERVIEW_GUIDE_METHODOLOGY 与"知情同意话术"字样。"""
    entry = informants.get_informant(itype)
    head = f"研究主题：{topic}\n\n"
    if research_questions:
        head += f"研究问题：{research_questions}\n\n"
    if discipline:
        head += f"学科取向：{discipline}\n\n"
    return (
        head
        + f"目标报道人类型：{itype}\n\n"
        f"该类型条目：\n{_entry_block(entry, itype)}\n\n"
        f"{informants.INTERVIEW_GUIDE_METHODOLOGY}\n\n"
        + assets.generation_prompt("guide_instruction")
    )


def compose_interview_guide(topic: str, informant_types: list[str], *, llm: Any = None,
                            research_questions: str = "", discipline: str = "") -> dict[str, str]:
    """每类报道人产出一份半结构式提纲；无 llm 或 llm error 分支都走模板降级。"""
    guides: dict[str, str] = {}
    for itype in informant_types:
        content = ""
        if llm is not None:
            messages = [
                {"role": "system", "content": assets.generation_prompt("guide_system")},
                {"role": "user", "content": _guide_prompt(
                    topic, itype, research_questions=research_questions,
                    discipline=discipline)},
            ]
            try:
                resp = llm.chat(messages, slot="interview", max_tokens=4096, temperature=0.5)
            except Exception:  # noqa: BLE001 —— 单类失败回落到该类模板，由 handler 标 degraded
                resp = None
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
    research_questions: str = ""
    discipline: str = ""


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
    if doc.degraded:
        # P0-1：LLM 失败与“未接入 LLM”同走 degraded 通道，文案由 compose_proposal
        # 按原因生成（如“模板降级版（LLM 调用失败：空响应（upstream 500））”）
        return Envelope(ok=True, data=doc, degraded=doc.degraded)
    return Envelope(ok=True, data=doc)


def guide_handler(topic: str, informant_types: list, *, llm: Any = None,
                  research_questions: str = "", discipline: str = "") -> Envelope:
    """registry 分派入口：逐类提纲折进 ``Envelope.data``（``dict[str, str]``）。

    P0-1 同款：任一类回落模板（无 llm / chat error / 空响应）都在包络里标
    degraded，而不是让调用方把模板提纲当成模型成品。
    """
    guides = compose_interview_guide(topic, informant_types or [], llm=llm,
                                     research_questions=research_questions,
                                     discipline=discipline)
    degraded = None
    if llm is None:
        degraded = "模板降级版（未接入 LLM）"
    elif any(TEMPLATE_DEGRADE_NOTE in body for body in guides.values()):
        degraded = "模板降级版（LLM 调用失败）"
    return Envelope(ok=True, data=guides, degraded=degraded)


register(CapabilityMeta(
    name="compose_proposal",
    description="按结构化上下文（研究者/田野点/行程/访谈对象/知识库五渠道）生成研究方案草稿；"
                "documents 是 kernel 内唯一 LLM 消费模块，无 key 时模板降级、chat error 分支同样降级；"
                "mode 目前只支持 global（英美式八章）；kaiti/grant 未实现，传了会回退 global "
                "并在 degraded 里标「体例降级」，不要向用户承诺已按开题/基金体例产出；"
                "降级时 degraded 会带 input_too_long / 调用异常等原因，须如实告知；"
                "参考文献仅限所给条目不得虚构；section 非空则仅重写该章。",
    fn=proposal_handler,
    args_model=ComposeProposalArgs,
))

register(CapabilityMeta(
    name="compose_interview_guide",
    description="为每类报道人（INFORMANT_LIBRARY 键）生成半结构式访谈提纲；"
                "user prompt 固定包含访谈方法要点与知情同意说明字样，"
                "无 llm 时走模板降级、直嵌条目字段与方法论全文。",
    fn=guide_handler,
    args_model=ComposeInterviewGuideArgs,
))
