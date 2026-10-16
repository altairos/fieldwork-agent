# tests/test_window.py — analyze_fieldwork_window 聚合契约（L2，全 mock）
from fieldwork_agent.kernel.aggregation.window import MAX_SAMPLES, _sample_dates
from fieldwork_agent.kernel.aggregation.window import _sample_curve
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem


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


def test_sample_dates_widen_exact_multiple():
    # span=112 恰为 16 的整数倍：原 ceil 公式得 step=7 → 17 点破硬顶；floor+1 得 step=8 → 15 点
    dates, step, widened = _sample_dates("2026-01-01", "2026-04-23", 7)
    assert widened is True and step == 8
    assert len(dates) == 15 and len(dates) <= MAX_SAMPLES
    assert dates[0] == "2026-01-01" and dates[-1] <= "2026-04-23"


def _ok(pairs):
    return Envelope(ok=True, data=ItemPage(
        items=[SearchItem(name=n, price=p) for n, p in pairs], total=len(pairs),
        cheapest=min(p for _, p in pairs)))


class CurveStub:
    def __init__(self, flight_price=600.0, hotel_price=150.0, fail_dates=()):
        self.flight_dates, self.hotel_dates = [], []
        self._f, self._h, self._fail = flight_price, hotel_price, set(fail_dates)

    def search_flights(self, **kw):
        self.flight_dates.append(kw.get("dep_date"))
        if kw.get("dep_date") in self._fail:
            return Envelope(ok=False, error={"code": "flyai_error", "message": "x"})
        return _ok([("MU", self._f)])

    def search_hotels(self, dest_name, **kw):
        self.hotel_dates.append(kw.get("check_in"))
        if kw.get("check_in") in self._fail:
            return Envelope(ok=False, error={"code": "flyai_error", "message": "x"})
        return _ok([("民宿", self._h)])


def test_sample_curve_parallel_calls():
    dates = ["2026-07-01", "2026-07-08", "2026-07-15"]
    stub = CurveStub(flight_price=620.0, hotel_price=140.0)
    curve, notes = _sample_curve(stub, "昆明", "西昌", dates)
    assert sorted(stub.flight_dates) == dates and sorted(stub.hotel_dates) == dates
    assert curve.dates == dates
    assert curve.flight_min == [620.0, 620.0, 620.0]
    assert curve.hotel_min == [140.0, 140.0, 140.0] and notes == []


def test_sample_curve_partial_failure_none():
    stub = CurveStub(fail_dates={"2026-07-08"})
    curve, notes = _sample_curve(stub, "昆明", "西昌", ["2026-07-01", "2026-07-08"])
    assert curve.flight_min == [600.0, None] and curve.hotel_min == [150.0, None]
    assert not notes  # 单点失败不进 notes（曲线缺值自表达）


def test_sample_curve_all_hotel_failed():
    class HotelDead(CurveStub):
        def search_hotels(self, dest_name, **kw):
            return Envelope(ok=False, error={"code": "flyai_error", "message": "x"})

    curve, notes = _sample_curve(HotelDead(), "昆明", "西昌", ["2026-07-01"])
    assert curve.hotel_min == [None] and any("住宿" in n for n in notes)
