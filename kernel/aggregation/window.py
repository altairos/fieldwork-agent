"""analyze_fieldwork_window — 调研时窗分析聚合能力（spec §4）。

多日期价格采样 × 节庆覆盖 × 预算可行性的联合推理；评分全确定性（LLM 不参与评分，
仅节庆抽取且可降级）。client/llm keyword-only 注入，不碰 repository（公理 1）。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fieldwork_agent.kernel.models import Envelope, PriceCurve

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
