"""compose_itinerary 聚合能力测试 — 全部基于 mock，不联网、不调 subprocess。"""

from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.aggregation import itinerary as itinerary_module
from fieldwork_agent.kernel.aggregation.itinerary import (
    compose_itinerary,
    live_flight_ref,
    live_hotel_ref,
    live_train_ref,
)
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem


def _ok(pairs):
    return Envelope(ok=True, data=ItemPage(
        items=[SearchItem(name=n, price=p) for n, p in pairs], total=len(pairs)))


class StubClient:  # 照 brief 原样：纯搜索替身，自身不提供 profile_field_site
    def search_flights(self, **kw): return _ok([("MU", 600.0)])
    def search_trains(self, **kw): return _ok([("G29", 350.0)])
    def search_hotels(self, dest_name, **kw): return _ok([("民宿", 150.0)])
    def search_pois(self, city_name, **kw): return _ok([("祠堂", None)])
    def search_keyword(self, query): return _ok([("民俗", None)])
    def search_ai(self, query): return _ok([])


def _stub_hotels(guest_name, guest_price, hotel_name, hotel_price):
    """民宿策略与通用策略各返回不同报价的替身客户端（用于价格优先用例）。"""
    class HotelStub(StubClient):
        def search_hotels(self, dest_name, **kw):
            if "民宿" in kw.get("hotel_types", ""):
                return _ok([(guest_name, guest_price)])
            return _ok([(hotel_name, hotel_price)])

    return HotelStub()


def test_dates_across_month_boundary():
    env = compose_itinerary("昆明", ["大理"], "2026-11-28", 6, 20000, client=StubClient())
    plan = env.data
    assert plan.end_date == "2026-12-03"          # 旧实现此处会拼出非法日期
    assert plan.stays[0]["check_out"] == "2026-12-03"


def test_rhythm_and_budget():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 30, 20000, client=StubClient())
    p = env.data
    labels = [s.label for s in p.rhythm]
    assert labels == ["适应期", "深入期", "补充期"] and sum(s.days for s in p.rhythm) <= 30
    assert p.buffer_days >= 1
    assert p.budget.items and abs(p.budget.total - sum(i.amount for i in p.budget.items)) < 0.01
    assert p.budget.over_budget is (p.budget.total > 20000)
    assert p.budget.coverage == "complete"  # 全报价可得（R2）


def test_legs_pick_cheapest_transport():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000, client=StubClient())
    leg = env.data.legs[0]
    assert leg["chosen"]["price"] == 350.0 and leg["alternatives"]  # 火车600→选350, 保留备选


# ────────────────────────────────────────────────────────────────
# 以下为补充测试：日期铁律 / 多点滚动 / 节奏合并 / 预算口径 /
# 失败隔离 / alternatives 填充 / 入参校验 / 注册面
# ────────────────────────────────────────────────────────────────


def test_dates_across_year_boundary():
    """跨年：旧实现固定 "2026-12-{...}" 必然错年份。"""
    env = compose_itinerary("昆明", ["大理"], "2026-12-30", 6, 20000, client=StubClient())
    assert env.data.end_date == "2027-01-04"
    assert env.data.stays[0]["check_out"] == "2027-01-04"


def test_dates_handle_leap_day():
    """闰日：2 月只有 29 天的年份同样按 timedelta 滚动，不做任何字符串拼月。"""
    env = compose_itinerary("昆明", ["大理"], "2028-02-27", 3, 20000, client=StubClient())
    assert env.data.end_date == "2028-02-29"
    assert env.data.stays[0]["check_out"] == "2028-02-29"


def test_multi_dest_rolling_dates_and_legs():
    env = compose_itinerary("昆明", ["大理", "丽江"], "2026-11-01", 10, 20000, client=StubClient())
    p = env.data
    # 住宿夜数 = 天数-1（末日退房返程），两点均分 9 → 5 + 4，窗口首尾相接
    assert [(s["nights"], s["check_in"], s["check_out"]) for s in p.stays] == [
        (5, "2026-11-01", "2026-11-06"), (4, "2026-11-06", "2026-11-10")]
    assert p.stays[-1]["check_out"] == p.end_date == "2026-11-10"
    # 腿 = 去程 + 中间跳 + 返程，日期取自滚动窗口
    assert [(x["from"], x["to"], x["date"], x["direction"]) for x in p.legs] == [
        ("昆明", "大理", "2026-11-01", "outbound"),
        ("大理", "丽江", "2026-11-06", "inter"),
        ("丽江", "昆明", "2026-11-10", "return")]


def test_rhythm_drops_zero_day_segments():
    """30/50/20 取整后为 0 天的段并入前段，不留空段。"""
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 3, 20000, client=StubClient())
    rhythm = env.data.rhythm
    assert [s.label for s in rhythm] == ["深入期"]
    assert all(s.days > 0 for s in rhythm) and sum(s.days for s in rhythm) <= 3
    # 1 天行程三段全为 0 → 兜底给唯一可排的深入期
    one = compose_itinerary("昆明", ["大理"], "2026-11-01", 1, 20000, client=StubClient())
    assert [s.label for s in one.data.rhythm] == ["深入期"]
    assert one.data.rhythm[0].days == 1


def test_buffer_days_formula():
    assert compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000,
                             client=StubClient()).data.buffer_days == 1
    assert compose_itinerary("昆明", ["大理"], "2026-11-01", 30, 20000,
                             client=StubClient()).data.buffer_days == 3


def test_budget_items_formula_and_over_budget_flag():
    """R2 口径：交通=单价×人数、住宿=晚价×夜数×房间数、市内与餐饮=人均日标准×人数×天数（估算）。"""
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 30, 20000, client=StubClient())
    b = env.data.budget
    by_name = {i.name: i for i in b.items}
    assert by_name["交通（昆明→大理）"].amount == 350.0       # 去程腿
    assert by_name["交通（大理→昆明）"].amount == 350.0       # 返程腿，两条腿合计=单价×人数×2
    assert by_name["住宿（大理）"].amount == 150.0 * 29       # 150/晚 × 29 夜 × 1 间
    meal = by_name["市内与餐饮"]
    assert meal.amount == 150.0 * 1 * 30                     # 可调人均日标准×人数×天数
    assert meal.source == "estimate" and meal.unit == "人均每日×人数×天数"
    assert all(i.currency == "CNY" for i in b.items)
    assert all(i.source in ("cheapest", "estimate") for i in b.items)
    assert b.total == 700.0 + 4350.0 + 4500.0
    assert b.over_budget is False and b.coverage == "complete"

    tight = compose_itinerary("昆明", ["大理"], "2026-11-01", 30, 1000, client=StubClient())
    assert tight.data.budget.budget_total == 1000
    assert tight.data.budget.over_budget is True  # complete 时才允许下超支结论


def test_coverage_partial_when_transport_unpriced():
    """R2/R6：缺关键报价时只能给已知小计，不得宣布预算内/超支。"""
    class NoTransportPrice(StubClient):
        def search_flights(self, **kw): return _ok([("MU", None)])
        def search_trains(self, **kw): return _ok([("G29", None)])

    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 200, client=NoTransportPrice())
    b = env.data.budget
    assert b.coverage == "partial"
    assert any("交通" in u for u in b.uncovered)
    assert b.over_budget is False  # 即便 total>budget_total 也不得在 partial 下宣布


def test_rooms_multiply_lodging_and_inputs_hash():
    """多人住宿需显式房间数口径（2 人/间假设）；R3 输入哈希随产物落盘。"""
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000, 3, client=StubClient())
    b = env.data.budget
    lodging = next(i for i in b.items if i.name.startswith("住宿"))
    assert lodging.amount == 150.0 * 9 * 2          # 3 人→2 间（ceil(3/2)）
    assert lodging.people == 2 and lodging.quantity == 9
    assert len(env.data.inputs_hash) == 40


def test_long_stay_flagged_unverified():
    """R6：三个月驻村只有短住日价→标估算/待核验，不宣称长住已落实。"""
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 90, 60000, client=StubClient())
    stay = env.data.stays[0]
    assert stay["nights"] > 14 and stay["long_stay_unverified"] is True
    assert env.degraded and "长住" in env.degraded


def test_budget_scales_with_researcher_count():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000, 3, client=StubClient())
    by_name = {i.name: i.amount for i in env.data.budget.items}
    assert by_name["交通（昆明→大理）"] == 350.0 * 3          # 交通按人数计
    assert by_name["市内与餐饮"] == 150.0 * 3 * 10            # R2：估算条目同样按人数×天数的可解释口径


def test_stay_prefers_cheapest_and_breaks_tie_for_guesthouse():
    """住宿以最低价为准（民宿身份不凌驾价格），只有同价时才走驻村取向。"""
    p = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                          client=_stub_hotels("客栈", 200.0, "连锁酒店", 150.0)).data
    assert p.stays[0]["nightly_price"] == 150.0
    assert p.stays[0]["chosen"]["guesthouse"] is False

    tie = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                            client=_stub_hotels("村里的民宿", 150.0, "连锁酒店", 150.0)).data
    assert tie.stays[0]["chosen"]["name"] == "村里的民宿"


def test_leg_without_price_degrades_instead_of_raising():
    """单腿无价可算：chosen=None、保留无价备选、预算跳过该条目，整体仍 ok=True。"""
    class NoPriceTransport(StubClient):
        def search_flights(self, **kw): return _ok([("MU", None)])
        def search_trains(self, **kw): return Envelope(ok=False)

    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000, client=NoPriceTransport())
    p = env.data
    assert env.ok is True
    assert p.legs[0]["chosen"] is None
    assert p.legs[0]["alternatives"] and p.legs[0]["note"]
    assert all(not i.name.startswith("交通") for i in p.budget.items)
    assert p.budget.coverage == "partial" and any("交通" in u for u in p.budget.uncovered)
    assert env.degraded and "交通" in env.degraded
    assert p.stays[0]["nightly_price"] == 150.0               # 住宿仍可算


def test_source_exception_is_isolated():
    class Raising(StubClient):
        def search_trains(self, **kw): raise RuntimeError("subprocess boom")
        def search_flights(self, **kw): raise RuntimeError("subprocess boom")

    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000, client=Raising())
    assert env.ok is True                       # 源异常不逃逸出模块
    assert env.data.legs[0]["chosen"] is None
    assert env.data.stays[0]["nights"] == 9


def test_all_sources_failed_returns_error_envelope():
    class AllFail:
        def search_hotels(self, dest_name, **kw): return Envelope(ok=False)
        def search_pois(self, city_name, **kw): return Envelope(ok=False)
        def search_ai(self, query): return Envelope(ok=False)
        def search_keyword(self, query): return Envelope(ok=False)
        def search_flights(self, **kw): return Envelope(ok=False)
        def search_trains(self, **kw): return Envelope(ok=False)

    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000, client=AllFail())
    assert env.ok is False
    assert env.error is not None and env.error.code == "all_sources_failed"
    assert env.error.message and env.error.hint  # error 必带 code+message（Task 1 约定）


def test_invalid_inputs_return_error_envelope():
    for kw, code in (
        ({"destinations": []}, "bad_input"),
        ({"duration_days": 0}, "bad_input"),
        ({"budget_total": 0}, "bad_input"),
        ({"researcher_count": 0}, "bad_input"),
        ({"start_date": "1970-13-01"}, "bad_start_date"),
    ):
        args = {"departure": "昆明", "destinations": ["大理"], "start_date": "2026-11-01",
                "duration_days": 10, "budget_total": 20000, "client": StubClient()}
        args.update(kw)
        env = compose_itinerary(**args)
        assert env.ok is False, args
        assert env.error.code == code, args
        assert env.error.message


def test_plan_alternatives_collect_unselected_transport():
    """plan.alternatives 由已抓到的未选交通候选汇成，零额外调用。"""
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000, client=StubClient())
    alts = env.data.alternatives
    assert alts  # StubClient 每腿给 flight(600)+train(350)，选 350 后 600 为次优
    # 每条目形如 {"from", "to", "items"}
    assert all({"from", "to", "items"} <= set(a) for a in alts)
    # 次优项（MU 600）被收入
    assert any(
        any(item["name"] == "MU" for item in a["items"])
        for a in alts
    ), "未选交通候选 MU 应出现在 plan.alternatives 中"


def test_registered_in_build_tool_schemas():
    """模块导入时 register() 生效：registry 是 Plan2/3 的接入面。"""
    tool = [t for t in build_tool_schemas() if t["function"]["name"] == "compose_itinerary"]
    assert tool, "compose_itinerary 应已注册"
    props = tool[0]["function"]["parameters"]["properties"]
    assert {"departure", "destinations", "start_date", "duration_days", "budget_total"} \
        <= set(props)
    assert "client" not in props  # 注入参数不进 schema
    assert props["researcher_count"]["default"] == 1


def test_default_client_self_built(monkeypatch):
    """client=None 时自建 FlyAIClient()（无 API Key 也不联网，测试用替身）。"""
    built = []

    class FakeFactory(StubClient):
        def __init__(self, api_key=""):
            built.append(api_key)

    monkeypatch.setattr(itinerary_module, "FlyAIClient", FakeFactory)
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000)
    assert built == [""]
    assert env.ok is True and env.data.end_date == "2026-11-06"


def test_generated_at_present():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000, client=StubClient())
    assert env.data.generated_at


# ────────────────────────────────────────────────────────────────
# R08 证据链：预算条目 source_ref 绑定实际选中报价的规格键
# ────────────────────────────────────────────────────────────────


def test_live_ref_key_formats():
    """三个纯函数规格键格式（R08 §6）：只依赖入参，无宿主依赖。"""
    assert live_flight_ref("昆明", "大理", "2026-11-01") == \
        "@live:search_flights:昆明>大理|2026-11-01"
    assert live_train_ref("大理", "昆明", "2026-11-06") == \
        "@live:search_trains:大理>昆明|2026-11-06"
    assert live_hotel_ref("大理", "2026-11-01", "2026-11-06") == \
        "@live:search_hotels:大理|2026-11-01|2026-11-06"
    assert live_hotel_ref("大理", "2026-11-01", "2026-11-06", "民宿,客栈") == \
        "@live:search_hotels:大理|2026-11-01|2026-11-06|民宿,客栈"


def test_budget_source_ref_binds_selected_quote():
    """金标准（6 天昆明→大理，StubClient）：source_ref 逐条携带选中报价的规格键。

    StubClient 最低价交通为 G29（火车 350 元，mode="train"）；住宿两策略同价 150 元，
    民宿策略候选 guesthouse=True 经 prefer_key 定序胜出 → 键带 民宿,客栈 尾段。
    """
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000, client=StubClient())
    by_name = {i.name: i for i in env.data.budget.items}
    assert by_name["交通（昆明→大理）"].source_ref == \
        "G29（@live:search_trains:昆明>大理|2026-11-01）"
    assert by_name["交通（大理→昆明）"].source_ref == \
        "G29（@live:search_trains:大理>昆明|2026-11-06）"
    assert by_name["住宿（大理）"].source_ref == \
        "民宿（@live:search_hotels:大理|2026-11-01|2026-11-06|民宿,客栈）"


def test_candidates_passthrough_jump_and_pic_urls():
    """A3：jump_url/pic_url 随候选 dict 透传到 chosen 与 alternatives；无链接源透 None 不炸。"""
    class LinkedStub(StubClient):
        def search_hotels(self, dest_name, **kw):
            if "民宿" in kw.get("hotel_types", ""):
                return Envelope(ok=True, data=ItemPage(items=[
                    SearchItem(name="洱海边民宿", price=150.0,
                               jump_url="https://hotel.example/minsu",
                               pic_url="https://pic.example/minsu.jpg"),
                ], total=1))
            return Envelope(ok=True, data=ItemPage(items=[
                SearchItem(name="古镇酒店", price=180.0,
                           jump_url="https://hotel.example/hotel"),
            ], total=1))

    plan = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000,
                             client=LinkedStub()).data
    stay = plan.stays[0]
    assert stay["chosen"]["name"] == "洱海边民宿"                  # 民宿 150 最低价当选
    assert stay["chosen"]["jump_url"] == "https://hotel.example/minsu"
    assert stay["chosen"]["pic_url"] == "https://pic.example/minsu.jpg"
    alt = [c for c in stay["alternatives"] if c["name"] == "古镇酒店"][0]
    assert alt["jump_url"] == "https://hotel.example/hotel"
    assert alt["pic_url"] is None                                  # 缺 pic_url 透 None
    leg = plan.legs[0]
    assert leg["chosen"]["name"] == "G29"                          # 火车 350 < 机票 600
    assert leg["chosen"]["jump_url"] is None                       # 交通源无链接（调研双证）→ 透 None
