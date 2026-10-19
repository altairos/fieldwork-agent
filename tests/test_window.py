# tests/test_window.py — analyze_fieldwork_window 聚合契约（L2，全 mock）
import pytest
from fieldwork_agent.kernel.aggregation.window import (
    MAX_SAMPLES,
    _extract_festivals,
    _parse_festival_json,
    _sample_curve,
    _sample_dates,
    _score_windows,
    analyze_fieldwork_window,
)
from fieldwork_agent.kernel.models import (
    Envelope,
    FestivalInfo,
    ItemPage,
    PriceCurve,
    SearchItem,
    ToolError,
    WindowAnalysis,
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
    prices = [p for _, p in pairs if p is not None]
    return Envelope(ok=True, data=ItemPage(
        items=[SearchItem(name=n, price=p) for n, p in pairs], total=len(pairs),
        cheapest=min(prices) if prices else None))


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
                         approx_end="2026-07-22", relevance="西南民族志核心节庆",
                         date_status="confirmed_by_user", basis="用户提供活动安排", source_ref="user:fixture")
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
                        approx_end="2026-07-04", relevance="x", date_status="verified_source",
                        basis="已核验材料夹具", source_ref="fixture:event")]
    with_fest = _score_windows(curve, far, **kw)
    # 窗口 [07-08,07-09]：avg_w=250，avg_all=375，d=1/3 → 60·(1/3)/0.3=66.7 触顶
    assert no_fest[0].evidence["price_score"] == pytest.approx(60.0)
    assert with_fest[0].evidence["price_score"] == pytest.approx(40.0)
    assert with_fest[0].evidence["festival_score"] == 0.0  # 不交集不计分


def test_score_windows_flight_dimension_dropped():
    """航班整类 None：partial 语义——estimate=None、budget_score=0、reasons 明示缺价（R07）。"""
    curve = _curve(["2026-07-01", "2026-07-08"], [None, None], [200.0, 100.0])
    windows = _score_windows(curve, [], "2026-07-01", "2026-07-15",
                             duration_days=2, budget_total=5000.0, sample_step=7)
    top = windows[0]  # [07-08,07-09]
    assert top.evidence["flight_min"] is None
    assert top.evidence["coverage"] == "partial"
    assert top.evidence["uncovered"] == ["flight"]
    assert top.evidence["estimate"] is None
    assert top.evidence["estimate_known"] == pytest.approx(400.0)  # 100×1 夜 + 150×2 天
    assert top.evidence["budget_score"] == 0.0
    assert "缺 flight 报价" in "；".join(top.reasons)


# ── Task 7 节庆抽取 ──────────────────────────────────────


class FakeLLM:
    """llm.chat 四键契约替身（错误场景附 error 键）。"""

    def __init__(self, content: str, error: str | None = None):
        self._content, self._error = content, error
        self.calls: list[dict] = []

    def chat(self, messages, *, slot="proposal", tools=None,
             temperature=0.7, max_tokens=4096) -> dict:
        self.calls.append({"messages": messages, "slot": slot,
                           "temperature": temperature})
        return {"content": self._content, "tool_calls": None,
                "finish_reason": "stop", "usage": {}, "error": self._error}


def test_parse_festival_json_fenced_and_noisy():
    """容忍围栏/前后噪声；非 ISO 日期与起止倒挂的条目丢弃。"""
    text = ('好的，以下是抽取结果：\n```json\n'
            '[{"name": "火把节", "approx_start": "2026-07-20", '
            ' "approx_end": "2026-07-22", "relevance": "彝族核心节庆"},'
            ' {"name": "坏日期", "approx_start": "7月20日", "approx_end": "2026-07-22"},'
            ' {"name": "倒挂", "approx_start": "2026-07-25", "approx_end": "2026-07-20"}]\n'
            '```\n希望能帮到你。')
    got = _parse_festival_json(text)
    assert len(got) == 1 and got[0].name == "火把节"
    assert got[0].approx_start == "2026-07-20"


def test_parse_festival_json_empty_and_garbage():
    assert _parse_festival_json("[]") == []
    assert _parse_festival_json("模型拒绝回答") == []


def test_parse_festival_json_approx_dates_passthrough():
    """Fix-5：approx_dates（LLM 原文人类可读日期，spec §4.1）只透传不过滤；缺失默认空串。"""
    text = ('[{"name": "火把节", "approx_start": "2026-07-20", '
            '"approx_end": "2026-07-22", "approx_dates": "农历六月廿四，约 7 月下旬",'
            ' "relevance": "r"},'
            ' {"name": "赶秋节", "approx_start": "2026-08-01",'
            ' "approx_end": "2026-08-02"}]')
    got = _parse_festival_json(text)
    assert len(got) == 2
    assert got[0].approx_dates == "农历六月廿四，约 7 月下旬"
    assert got[1].approx_dates == ""


def test_extract_festivals_happy_path():
    llm = FakeLLM('```json\n[{"name": "火把节", "approx_start": "2026-07-20",'
                  ' "approx_end": "2026-07-22", "relevance": "r"}]\n```')
    festivals, degraded = _extract_festivals(
        llm, "西昌", "2026-07-01", "2026-07-29",
        ["西昌火把节 7 月下旬开幕", "彝族风情周活动日历"])
    assert degraded and len(festivals) == 1
    assert festivals[0].date_status == "unverified" and festivals[0].approx_start == ""
    assert llm.calls[0]["slot"] == "interview"
    assert llm.calls[0]["temperature"] == 0.3
    assert "西昌" in llm.calls[0]["messages"][0]["content"]
    assert "彝族风情周" in llm.calls[0]["messages"][0]["content"]  # hints 入 prompt


def test_extract_festivals_degrades():
    """llm 未注入 / chat 报错：整体降级为空，返回说明。"""
    a, note_a = _extract_festivals(None, "西昌", "2026-07-01", "2026-07-29", [])
    assert a == [] and note_a
    llm_err = FakeLLM("", error="quota exceeded")
    b, note_b = _extract_festivals(llm_err, "西昌", "2026-07-01", "2026-07-29", [])
    assert b == [] and note_b


# ── Task 8 主函数 ──────────────────────────────────────


class AnalyzeStub:
    """采样+关键词搜索替身：手算金标准同款曲线。"""
    DATES = ["2026-07-01", "2026-07-08", "2026-07-15", "2026-07-22", "2026-07-29"]

    def __init__(self):
        self._f = [700.0, 680.0, 650.0, 560.0, 600.0]
        self._h = [180.0, 170.0, 160.0, 130.0, 150.0]
        self.kw_queries: list[str] = []

    def search_flights(self, origin, destination, dep_date="", **kw):
        return _ok([("MU", self._f[self.DATES.index(dep_date)])])

    def search_hotels(self, dest_name, check_in="", **kw):
        return _ok([("民宿", self._h[self.DATES.index(check_in)])])

    def search_keyword(self, query="", **kw):
        self.kw_queries.append(query)
        return _ok([("西昌火把节 7 月下旬", None), ("彝族风情周", None)])


def test_analyze_fieldwork_window_happy():
    stub = AnalyzeStub()
    llm = FakeLLM('```json\n[{"name": "彝族火把节", "approx_start": "2026-07-20",'
                  ' "approx_end": "2026-07-22", "relevance": "r"}]\n```')
    env = analyze_fieldwork_window("昆明", "西昌", "2026-07-01", "2026-07-29",
                                   duration_days=10, budget_total=5000.0,
                                   sample_step=7, client=stub, llm=llm)
    assert env.ok and isinstance(env.data, WindowAnalysis)
    assert env.data.windows[0].start == "2026-07-15"
    assert env.data.windows[0].score == pytest.approx(33.13, abs=0.01)
    assert len(env.data.price_curve.dates) == 4
    assert env.data.windows[0].evidence["quote_check"]["coverage"] == "partial"
    assert len(env.data.windows) == 3
    assert env.data.festivals[0].name == "彝族火把节"
    assert env.data.budget_feasible is None
    assert env.data.degraded and env.data.festivals[0].approx_start == ""
    assert any("节庆" in q for q in stub.kw_queries)
    assert env.data.price_curve.flight_min[0] == 700.0


def test_analyze_fieldwork_window_bad_args():
    kw = dict(departure="昆明", destination="西昌", duration_days=10, budget_total=5000.0)
    env = analyze_fieldwork_window(range_start="2026-07-29", range_end="2026-07-01", **kw)
    assert env.ok is False and env.error.code == "bad_range"
    env = analyze_fieldwork_window(range_start="2026/07/01", range_end="2026-07-29", **kw)
    assert env.ok is False and env.error.code == "bad_date"
    env = analyze_fieldwork_window(range_start="2026-07-01", range_end="2026-07-29",
                                   duration_days=40, budget_total=5000.0,
                                   departure="昆明", destination="西昌")
    assert env.ok is False and env.error.code == "bad_duration"  # 区间 29 天装不下 40 天
    env = analyze_fieldwork_window(range_start="2026-07-01", range_end="2026-07-29",
                                   **kw, sample_step=0)
    assert env.ok is False and env.error.code == "bad_step"


def test_analyze_fieldwork_window_all_sources_failed():
    class Dead(AnalyzeStub):
        def search_flights(self, origin, destination, dep_date="", **kw):
            return Envelope(ok=False,
                            error=ToolError(code="flyai_error", message="x", hint=""))

        def search_hotels(self, dest_name, check_in="", **kw):
            return Envelope(ok=False,
                            error=ToolError(code="flyai_error", message="x", hint=""))

    env = analyze_fieldwork_window("昆明", "西昌", "2026-07-01", "2026-07-29",
                                   duration_days=10, budget_total=5000.0,
                                   client=Dead(), llm=None)
    assert env.ok is False and env.error.code == "all_sources_failed"


def test_analyze_budget_partial_flights_dead():
    """仅航班失败（住宿在）：budget_feasible=None + degraded 明示缺价（R07 三态）。"""
    class FlightsDead(AnalyzeStub):
        def search_flights(self, origin, destination, dep_date="", **kw):
            return Envelope(ok=False,
                            error=ToolError(code="flyai_error", message="x", hint=""))

    env = analyze_fieldwork_window("昆明", "西昌", "2026-07-01", "2026-07-29",
                                   duration_days=10, budget_total=5000.0,
                                   client=FlightsDead(), llm=None)
    assert env.ok
    assert env.data.budget_feasible is None
    assert env.data.degraded and "缺 flight 报价" in env.data.degraded
    assert env.degraded == env.data.degraded  # Envelope 级与 data 级同源
    assert env.data.windows[0].evidence["estimate"] is None
    assert env.data.windows[0].evidence["estimate_known"] > 0


def test_analyze_fieldwork_window_registered():
    from fieldwork_agent.kernel.registry import capabilities

    meta = capabilities()["analyze_fieldwork_window"]
    assert meta.fn is analyze_fieldwork_window
    assert meta.read_only is True and meta.open_world is True
