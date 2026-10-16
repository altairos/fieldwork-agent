"""analyze_fieldwork_window — 调研时窗分析聚合能力（spec §4）。

多日期价格采样 × 节庆覆盖 × 预算可行性的联合推理；评分全确定性（LLM 不参与评分，
仅节庆抽取且可降级）。client/llm keyword-only 注入，不碰 repository（公理 1）。
"""
from datetime import datetime, timedelta

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
