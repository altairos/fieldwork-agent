"""analyze_fieldwork_window — 调研时窗分析聚合能力（spec §4）。

多日期价格采样 × 节庆覆盖 × 预算可行性的联合推理；评分全确定性（LLM 不参与评分，
仅节庆抽取且可降级）。client/llm keyword-only 注入，不碰 repository（公理 1）。
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fieldwork_agent.kernel.llm import LLMClient
from fieldwork_agent.kernel.models import (
    Envelope,
    FestivalInfo,
    PriceCurve,
    RecommendedWindow,
    ToolError,
    WindowAnalysis,
)
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from fieldwork_agent.kernel.sources.flyai import FlyAIClient
from pydantic import BaseModel, Field

DATE_FMT = "%Y-%m-%d"
MAX_SAMPLES = 16


def _sample_dates(range_start: str, range_end: str, sample_step: int) -> tuple[list[str], int, bool]:
    """在 [range_start, range_end] 上按步进生成采样日期序列。

    点数会超 MAX_SAMPLES 时自动放大步进（floor(span/MAX_SAMPLES)+1）并返回 widened=True。
    """
    start = datetime.strptime(range_start, DATE_FMT).date()
    end = datetime.strptime(range_end, DATE_FMT).date()
    if end < start:
        raise ValueError(f"range_end {range_end} 早于 range_start {range_start}")
    if sample_step < 1:
        sample_step = 1
    span = (end - start).days
    widened = span // sample_step + 1 > MAX_SAMPLES
    step = span // MAX_SAMPLES + 1 if widened else sample_step
    dates = [(start + timedelta(days=i * step)).isoformat()
             for i in range(span // step + 1)]
    return dates, step, widened


def _sample_curve(client, departure: str, destination: str,
                  dates: list[str]) -> tuple[PriceCurve, list[str]]:
    """并行采样：每个日期点查一次航班（dep_date）+ 一次住宿（check_in）。

    dict 按 (tag, date) 索引存放结果，杜绝线程完成顺序干扰；单点失败记 None；
    某维度整类全 None 时记 note（评分层据此折叠维度）。
    """
    results: dict[tuple[str, str], float | None] = {}

    def _one(tag: str, date: str):
        env = (client.search_flights(origin=departure, destination=destination, dep_date=date)
               if tag == "flight" else
               client.search_hotels(dest_name=destination, check_in=date))
        results[(tag, date)] = (env.data.cheapest if getattr(env, "ok", False)
                                and getattr(env.data, "cheapest", None) is not None else None)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for d in dates:
            pool.submit(_one, "flight", d)
            pool.submit(_one, "hotel", d)

    notes: list[str] = []
    if all(results.get(("flight", d)) is None for d in dates):
        notes.append("航班采样整类失败，评分剔除交通维度")
    if all(results.get(("hotel", d)) is None for d in dates):
        notes.append("住宿采样整类失败，评分剔除住宿维度")
    curve = PriceCurve(
        dates=list(dates),
        flight_min=[results.get(("flight", d)) for d in dates],
        hotel_min=[results.get(("hotel", d)) for d in dates],
    )
    return curve, notes


def _avg(nums: list[float]) -> float | None:
    return sum(nums) / len(nums) if nums else None


def _nearest(dates: list[str], target: str) -> int | None:
    """距 target 最近的采样点下标（平局取更早）；空列表返回 None。"""
    if not dates:
        return None
    t = datetime.strptime(target, DATE_FMT).date()
    best_i, best_gap = 0, None
    for i, d in enumerate(dates):
        gap = abs((datetime.strptime(d, DATE_FMT).date() - t).days)
        if best_gap is None or gap < best_gap:  # 严格小于 → 平局保留更早
            best_i, best_gap = i, gap
    return best_i


def _window_values(values: list[float | None], dates: list[str],
                   w_start: str, w_end: str) -> list[float | None]:
    """窗口 [w_start, w_end]（含端点）覆盖的采样值；无覆盖点时回退最近点。"""
    s = datetime.strptime(w_start, DATE_FMT).date()
    e = datetime.strptime(w_end, DATE_FMT).date()
    picked = [v for d, v in zip(dates, values, strict=False)
              if s <= datetime.strptime(d, DATE_FMT).date() <= e]
    if not picked:
        i = _nearest(dates, w_start)
        if i is not None:
            picked = [values[i]]
    return picked


def _score_windows(curve: PriceCurve, festivals: list[FestivalInfo],
                   range_start: str, range_end: str, duration_days: int,
                   budget_total: float, sample_step: int,
                   meal_daily: float = 150.0) -> list[RecommendedWindow]:
    """滑动窗口枚举 + 三维确定性评分（spec §4.2，LLM 不参与）。"""
    dates = curve.dates
    f_all = [v for v in curve.flight_min if v is not None]
    h_all = [v for v in curve.hotel_min if v is not None]
    avg_flight, avg_hotel = _avg(f_all), _avg(h_all)
    avg_all = _avg([a for a in (avg_flight, avg_hotel) if a is not None])
    cap = 40.0 if festivals else 60.0
    nights = duration_days - 1

    windows: list[RecommendedWindow] = []
    cur = datetime.strptime(range_start, DATE_FMT).date()
    last_start = datetime.strptime(range_end, DATE_FMT).date() - timedelta(days=duration_days - 1)
    while cur <= last_start:
        we = cur + timedelta(days=duration_days - 1)
        w_start, w_end = cur.isoformat(), we.isoformat()
        f_vals = [v for v in _window_values(curve.flight_min, dates, w_start, w_end)
                  if v is not None]
        h_vals = [v for v in _window_values(curve.hotel_min, dates, w_start, w_end)
                  if v is not None]
        w_bases = [a for a in (_avg(f_vals) if avg_flight is not None and f_vals else None,
                               _avg(h_vals) if avg_hotel is not None and h_vals else None)
                   if a is not None]
        avg_w = _avg(w_bases)
        if avg_all and avg_w:
            d = 1 - avg_w / avg_all
            price_score = max(0.0, min(cap, cap * d / 0.3))
        else:
            price_score = 0.0
        hits = [f for f in festivals
                if datetime.strptime(f.approx_start, DATE_FMT).date() <= we
                and datetime.strptime(f.approx_end, DATE_FMT).date() >= cur]
        festival_score = min(30.0, 15.0 * len(hits))
        f_min = min(f_vals) if f_vals else None
        h_min = min(h_vals) if h_vals else None
        est = ((f_min * 2 if f_min is not None else 0.0)
               + (h_min * nights if h_min is not None else 0.0)
               + meal_daily * duration_days)
        r = est / budget_total if budget_total > 0 else None
        budget_score = max(0.0, 30.0 * (1 - r)) if r is not None else 0.0
        reasons: list[str] = []
        if price_score > 0 and avg_w is not None and avg_all:
            reasons.append(f"窗口均价低于全期均价 {round((1 - avg_w / avg_all) * 100, 1)}%")
        reasons.extend(f"覆盖「{f.name}」（{f.approx_start}~{f.approx_end}）" for f in hits)
        if r is not None:
            verdict = "预算内" if r <= 1 else "超预算"
            reasons.append(f"{verdict}：估算 ¥{est:.0f} / 预算 ¥{budget_total:.0f}")
        windows.append(RecommendedWindow(
            start=w_start, end=w_end,
            score=round(price_score + festival_score + budget_score, 2),
            reasons=reasons,
            evidence={"price_score": round(price_score, 2),
                      "festival_score": festival_score,
                      "budget_score": round(budget_score, 2),
                      "flight_min": f_min, "hotel_min": h_min,
                      "estimate": round(est, 2),
                      "festivals": [f.name for f in hits]},
        ))
        cur += timedelta(days=sample_step)
    windows.sort(key=lambda w: (-w.score, w.start))
    return windows


def _parse_festival_json(text: str) -> list[FestivalInfo]:
    """从 LLM 输出解析节庆数组；容忍围栏/前后噪声，非法条目丢弃。"""
    body = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", body, re.DOTALL)
    if fence:
        body = fence.group(1)
    start, end = body.find("["), body.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        raw = json.loads(body[start:end + 1])
    except json.JSONDecodeError:
        return []
    out: list[FestivalInfo] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        s, e = str(entry.get("approx_start") or ""), str(entry.get("approx_end") or "")
        try:
            sd = datetime.strptime(s, DATE_FMT).date()
            ed = datetime.strptime(e, DATE_FMT).date()
        except ValueError:
            continue
        if not name or sd > ed:
            continue
        out.append(FestivalInfo(name=name, approx_start=s, approx_end=e,
                                relevance=str(entry.get("relevance") or "")))
    return out


def _extract_festivals(llm, destination: str, range_start: str, range_end: str,
                       hints: list[str]) -> tuple[list[FestivalInfo], str | None]:
    """LLM 从关键词搜索条目抽取节庆（spec §4.3：失败整体降级，不影响评分）。"""
    if llm is None:
        return [], "LLM 未注入，节庆维度降级为空"
    lines = "\n".join(f"- {h}" for h in hints) or "-（无搜索结果）"
    prompt = (
        f"你在为田野调研挑选时间窗口。目的地：{destination}；"
        f"调研区间：{range_start} 至 {range_end}。\n"
        f"以下是旅游平台关键词搜索的原始条目：\n{lines}\n\n"
        "请从中抽取调研区间内（含跨界）将举办的当地节庆/民俗/大型活动，"
        '仅输出 JSON 数组，每项形如 {"name":"节庆名","approx_start":"YYYY-MM-DD",'
        '"approx_end":"YYYY-MM-DD","relevance":"一句话调研价值"}；'
        "日期不确定就给最可能的估计区间。没有可抽取项时输出 []。不要输出其他文字。"
    )
    env = llm.chat(messages=[{"role": "user", "content": prompt}],
                   slot="interview", temperature=0.3)
    content = str(env.get("content") or "")
    if env.get("error") or not content.strip():
        return [], "节庆抽取失败，节庆维度降级为空"
    return _parse_festival_json(content), None


# ── Task 8 主函数 + 注册（M2 收口，能力面 13→14）──────────


def _error_env(code: str, message: str, hint: str = "请检查参数后重试") -> Envelope:
    return Envelope(ok=False, error=ToolError(code=code, message=message, hint=hint))


class AnalyzeFieldworkWindowArgs(BaseModel):
    """analyze_fieldwork_window 的 args_model（schema 单一来源，spec §3.1）。"""
    departure: str = Field(description="出发城市，如 昆明")
    destination: str = Field(description="调研目的地城市，如 西昌")
    range_start: str = Field(description="可选调研区间开始日期 YYYY-MM-DD")
    range_end: str = Field(description="可选调研区间结束日期 YYYY-MM-DD")
    duration_days: int = Field(ge=1, le=90, description="单次驻村时长（天）")
    budget_total: float = Field(gt=0, description="总预算（元）")
    sample_step: int = Field(default=7, ge=1, description="采样步进（天），默认 7")


def analyze_fieldwork_window(departure: str, destination: str, range_start: str,
                             range_end: str, duration_days: int, budget_total: float,
                             sample_step: int = 7, *, client=None, llm=None) -> Envelope:
    """多日期价格采样 × 节庆覆盖 × 预算可行性的时窗推荐（spec §4.1）。"""
    try:
        start = datetime.strptime(range_start, DATE_FMT).date()
        end = datetime.strptime(range_end, DATE_FMT).date()
    except (ValueError, TypeError):
        return _error_env("bad_date", "range_start/range_end 需为 YYYY-MM-DD")
    if end < start:
        return _error_env("bad_range", "range_end 早于 range_start")
    if duration_days < 1 or duration_days > (end - start).days + 1:
        return _error_env("bad_duration", "duration_days 需在 1..区间天数+1 之间")
    if budget_total <= 0:
        return _error_env("bad_budget", "budget_total 需为正数")
    if sample_step < 1:
        return _error_env("bad_step", "sample_step 需为正整数")

    c = client or FlyAIClient()
    if llm is None:
        try:
            llm = LLMClient()
        except Exception:  # 构造失败按未注入降级（公理：不抛异常）
            llm = None

    dates, _, widened = _sample_dates(range_start, range_end, sample_step)
    curve, notes = _sample_curve(c, departure, destination, dates)
    if all(v is None for v in curve.flight_min) and all(v is None for v in curve.hotel_min):
        return _error_env("all_sources_failed", "航班与住宿采样整类失败，无法生成时窗分析",
                          "可稍后重试或缩小日期范围")
    kw_env = c.search_keyword(query=f"{destination} 节庆 民俗 活动日历")
    items = kw_env.data.items if getattr(kw_env, "ok", False) and kw_env.data else []
    hints = [i.name for i in items if i.name][:8]
    festivals, fest_note = _extract_festivals(llm, destination, range_start, range_end, hints)
    windows = _score_windows(curve, festivals, range_start, range_end,
                             duration_days, budget_total, sample_step)
    degraded = list(notes)
    if fest_note:
        degraded.append(fest_note)
    if widened:
        degraded.append(f"区间采样点超 {MAX_SAMPLES}，步进自动放大")
    top = windows[:3]
    best_est = top[0].evidence.get("estimate") if top else None
    analysis = WindowAnalysis(
        windows=top,
        price_curve=curve,
        festivals=festivals,
        budget_feasible=bool(best_est is not None and best_est <= budget_total),
        degraded="；".join(degraded) or None,
    )
    return Envelope(ok=True, data=analysis)


register(CapabilityMeta(
    name="analyze_fieldwork_window",
    description="调研时窗分析：多日期价格采样×节庆覆盖×预算可行性，推荐调研出发窗口",
    fn=analyze_fieldwork_window,
    args_model=AnalyzeFieldworkWindowArgs,
    read_only=True,
    open_world=True,
))
