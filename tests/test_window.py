# tests/test_window.py — analyze_fieldwork_window 聚合契约（L2，全 mock）
from fieldwork_agent.kernel.aggregation.window import MAX_SAMPLES, _sample_dates


def test_sample_dates_basic():
    dates, step, widened = _sample_dates("2026-07-01", "2026-07-29", 7)
    assert dates == ["2026-07-01", "2026-07-08", "2026-07-15", "2026-07-22", "2026-07-29"]
    assert step == 7 and widened is False


def test_sample_dates_widen_over_cap():
    # span=119 天 / step7 → 18 点 > 16：放大步进 ceil(119/16)=8 → 15 点
    dates, step, widened = _sample_dates("2026-01-01", "2026-04-30", 7)
    assert widened is True and step == 8 and len(dates) <= MAX_SAMPLES
    assert dates[0] == "2026-01-01" and dates[-1] <= "2026-04-30"


def test_sample_dates_single_day():
    assert _sample_dates("2026-07-01", "2026-07-01", 7) == (["2026-07-01"], 7, False)
