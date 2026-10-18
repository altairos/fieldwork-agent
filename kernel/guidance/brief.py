"""有界项目摘要（F04）：纯 state 推导，kernel 无 repo 依赖。

供 CLI system 快照与 get_project_brief 工具共用。有界性：田野点/对象/快照
均取尾部少量条目并标注截断；研究问题全文保留（定位阶段核心信息）。
"""
from __future__ import annotations

from typing import Any

from fieldwork_agent.kernel.models import ResearchContext
from fieldwork_agent.kernel.guidance.phases import REQUIRED_FIELDS, PHASE_RESEARCH_POSITIONING

_BRIEF_SKIP_SLOTS = ("extras", "candidate_windows")


def build_project_brief(state: dict, *,
                        cached_searches: list[dict] | None = None) -> str:
    ctx = ResearchContext.model_validate(state.get("research_context") or {})
    lines: list[str] = []
    # 研究定位：非空槽位平铺（跳过 extras/candidate_windows）
    filled = [f"{k}={getattr(ctx, k)}" for k in (
        "discipline", "topic", "site_status", "scale", "budget", "time_window",
        "departure_city", "target_location", "travel_date", "purpose",
        "research_questions")
        if str(getattr(ctx, k) or "").strip()]
    filled += [f"{k}={v}" for k, v in (ctx.extras or {}).items()]
    lines.append("研究定位：" + ("；".join(filled) if filled else "（空）"))
    if ctx.research_questions.strip():
        lines.append(f"研究问题：{ctx.research_questions}")
    if ctx.candidate_windows:
        w = ctx.candidate_windows[0]
        lines.append(f"候选时窗（未经用户确认）：{w.get('start')} ~ {w.get('end')}；"
                     "确认采纳后写入 time_window")
    if ctx.interviews_not_applicable:
        lines.append(f"访谈不适用（理由：{ctx.interviews_na_reason or '未填'}）")
    required = REQUIRED_FIELDS.get(PHASE_RESEARCH_POSITIONING) or ()
    missing = [f for f in required
               if f not in ("extras",) and not str(getattr(ctx, f, "") or "").strip()]
    if missing:
        lines.append("定位待补：" + "、".join(missing))
    sites = state.get("field_sites") or []
    if sites:
        tail = sites[-3:]
        site_txt = "、".join(
            f"{s.get('name', '?')}{('（已确认）' if s.get('confirmed') else '')}"
            for s in tail)
        more = f"（共 {len(sites)} 个，尾部 3 个）" if len(sites) > 3 else ""
        lines.append(f"候选田野点{more}：{site_txt}")
    else:
        lines.append("候选田野点：（无）")
    infs = state.get("informants") or []
    if infs:
        codes = "、".join(str(i.get("code") or i.get("type", "?")) for i in infs[-3:])
        lines.append(f"访谈对象 {len(infs)} 位：{codes}")
    else:
        lines.append("访谈对象：（无）")
    props = state.get("proposals") or []
    if props:
        latest = props[-1]
        lines.append(f"方案草稿 {len(props)} 份（最新 {latest.get('id', '?')}"
                     f" v{latest.get('version', '?')}）")
    else:
        lines.append("方案草稿：（无）")
    snaps = cached_searches if cached_searches is not None else (state.get("cached_searches") or [])
    if snaps:
        tail = snaps[-5:]
        snap_txt = "；".join(f"{s.get('id', '?')}:{s.get('tool', '?')}"
                             f"({str(s.get('query', ''))[:60]})" for s in tail)
        more = "（尾部 5 条）" if len(snaps) > 5 else ""
        lines.append(f"已缓存搜索 {len(snaps)} 条{more}：{snap_txt}")
    return "项目摘要：" + "；".join(lines)
