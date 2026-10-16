"""analyze_fieldwork_window — 调研时窗分析聚合能力（spec §4）。

多日期价格采样 × 节庆覆盖 × 预算可行性的联合推理；评分全确定性（LLM 不参与评分，
仅节庆抽取且可降级）。client/llm keyword-only 注入，不碰 repository（公理 1）。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fieldwork_agent.kernel.models import FestivalInfo, PriceCurve, RecommendedWindow

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
