# tests/test_window.py — analyze_fieldwork_window 聚合契约（L2，全 mock）
import pytest
from fieldwork_agent.kernel.aggregation.window import (
    MAX_SAMPLES,
    _sample_curve,
    _sample_dates,
    _score_windows,
)
from fieldwork_agent.kernel.models import (
    Envelope,
    FestivalInfo,
    ItemPage,
    PriceCurve,
    SearchItem,
)


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


# ── Task 6 评分层 ────────────────────────────────────────


def _curve(dates, flight, hotel):
    return PriceCurve(dates=list(dates), flight_min=list(flight), hotel_min=list(hotel))


def test_score_windows_hand_computed():
    """手算金标准：全期 avg_all=(638+158)/2=398；窗口 W1[07-01,07-10]
    W2[07-08,07-17] W3[07-15,07-24]。W3: price=min(40,40·(1-375/398)/0.3)=7.71
    + 节庆 15 + budget=30·(1-3790/5000)=7.26 → 29.97；W2=4.56；W1=3.66。"""
    dates = ["2026-07-01", "2026-07-08", "2026-07-15", "2026-07-22", "2026-07-29"]
    curve = _curve(dates, [700.0, 680.0, 650.0, 560.0, 600.0],
                  [180.0, 170.0, 160.0, 130.0, 150.0])
    torch = FestivalInfo(name="彝族火把节", approx_start="2026-07-20",
                         approx_end="2026-07-22", relevance="西南民族志核心节庆")
    windows = _score_windows(curve, [torch], "2026-07-01", "2026-07-29",
                             duration_days=10, budget_total=5000.0, sample_step=7)
    assert len(windows) == 3  # 下一个 start=07-22 > last_start=07-20，恰三个
    top, mid, low = windows
    assert (top.start, top.end) == ("2026-07-15", "2026-07-24")
    assert top.score == pytest.approx(29.97, abs=0.01)
    assert mid.score == pytest.approx(4.56, abs=0.01)
    assert low.score == pytest.approx(3.66, abs=0.01)
    joined = "；".join(top.reasons)
    assert "火把节" in joined and "预算" in joined
    assert top.evidence["estimate"] == pytest.approx(3790.0)
    assert top.evidence["flight_min"] == pytest.approx(560.0)
    assert top.evidence["hotel_min"] == pytest.approx(130.0)


def test_score_windows_cap_by_festival_presence():
    """同一低价窗口：无节庆数据 cap=60 封顶；有节庆数据（即使不交集）cap=40。"""
    curve = _curve(["2026-07-01", "2026-07-08"], [800.0, 400.0], [200.0, 100.0])
    kw = dict(range_start="2026-07-01", range_end="2026-07-15",
              duration_days=2, budget_total=5000.0, sample_step=7)
    no_fest = _score_windows(curve, [], **kw)
    far = [FestivalInfo(name="赶秋节", approx_start="2026-07-03",
                        approx_end="2026-07-04", relevance="x")]
    with_fest = _score_windows(curve, far, **kw)
    # 窗口 [07-08,07-09]：avg_w=250，avg_all=375，d=1/3 → 60·(1/3)/0.3=66.7 触顶
    assert no_fest[0].evidence["price_score"] == pytest.approx(60.0)
    assert with_fest[0].evidence["price_score"] == pytest.approx(40.0)
    assert with_fest[0].evidence["festival_score"] == 0.0  # 不交集不计分


def test_score_windows_flight_dimension_dropped():
    """航班整类 None：交通维度剔除，est 只含住宿+餐费。"""
    curve = _curve(["2026-07-01", "2026-07-08"], [None, None], [200.0, 100.0])
    windows = _score_windows(curve, [], "2026-07-01", "2026-07-15",
                             duration_days=2, budget_total=5000.0, sample_step=7)
    top = windows[0]  # [07-08,07-09]
    assert top.evidence["flight_min"] is None
    assert top.evidence["estimate"] == pytest.approx(400.0)  # 100×1 夜 + 150×2 天
