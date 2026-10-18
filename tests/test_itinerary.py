"""compose_itinerary 聚合能力测试 — 全部基于 mock，不联网、不调 subprocess。"""

from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.aggregation import itinerary as itinerary_module
from fieldwork_agent.kernel.aggregation.itinerary import (
    compose_itinerary,
    live_flight_ref,
    live_hotel_ref,
    live_train_ref,
)
from fieldwork_agent.kernel.models import (
    Envelope,
    GeoLocation,
    ItemPage,
    SearchItem,
    ToolError,
)


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
    """R2 口径：交通=单价×人数、住宿=晚价×夜数×房间数、餐饮/市内交通=表值区间×人数×天数。"""
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 30, 20000, client=StubClient())
    b = env.data.budget
    by_name = {i.name: i for i in b.items}
    assert by_name["交通（昆明→大理）"].amount == 350.0       # 去程腿
    assert by_name["交通（大理→昆明）"].amount == 350.0       # 返程腿
    assert by_name["住宿（大理）"].amount == 150.0 * 29       # 150/晚 × 29 夜 × 1 间
    food = by_name["餐饮（估算）"]
    assert food.amount == 18.0 * 1 * 30                      # village+mixed 下沿
    assert food.amount_low == 18.0 * 30 and food.amount_high == 30.0 * 30
    assert food.source == "estimate" and food.unit == "元/人·天×人数×天数"
    assert "默认口径" in food.basis and "14.7" in food.basis
    local = by_name["市内交通（估算）"]
    assert local.amount == 0.0 and local.amount_high == 5.0 * 30
    assert all(i.currency == "CNY" for i in b.items)
    assert all(i.source in ("cheapest", "estimate") for i in b.items)
    assert b.total == 700.0 + 4350.0 + 540.0 + 0.0
    assert b.total_high == 700.0 + 4350.0 + 900.0 + 150.0
    assert b.over_budget is False and b.over_budget_possible is False
    assert b.coverage == "complete"

    tight = compose_itinerary("昆明", ["大理"], "2026-11-01", 30, 1000, client=StubClient())
    assert tight.data.budget.budget_total == 1000
    assert tight.data.budget.over_budget is True          # 下沿已超
    assert tight.data.budget.over_budget_possible is True


def test_explicit_meal_override_replaces_meal_range_only():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                            meals_local_per_person_day=30, client=StubClient())
    by_name = {i.name: i for i in env.data.budget.items}
    food = by_name["餐饮（估算）"]
    assert food.amount_low == food.amount_high == 30.0 * 10
    assert "人工指定" in food.basis
    # override 只作用于餐标：市内交通仍按表值区间（village 0-5 元/人·天）
    assert by_name["市内交通（估算）"].amount_high == 5.0 * 10


def test_settlement_and_board_mode_shift_amounts():
    cheap = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                              settlement_type="village", board_mode="self_cook",
                              client=StubClient())
    dear = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                             settlement_type="city", board_mode="eat_out",
                             client=StubClient())
    a = next(i for i in cheap.data.budget.items if i.name == "餐饮（估算）")
    b = next(i for i in dear.data.budget.items if i.name == "餐饮（估算）")
    assert b.amount_low > a.amount_high
    assert "默认口径" not in b.basis


def test_local_transport_can_be_excluded():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                            include_local_transport=False, client=StubClient())
    assert not any(i.name == "市内交通（估算）" for i in env.data.budget.items)


def test_invalid_settlement_rejected():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                            settlement_type="hamlet", client=StubClient())
    assert env.ok is False and env.error.code == "bad_input"
    assert "settlement_type" in env.error.message


def test_duration_days_upper_bound_aligned_with_living_cost():
    """N08：行程天数上限与生活成本子能力对齐——365 天可算，366 天整体拒绝。

    旧实现行程接受任意正天数，366 天时 living_cost 返回 ok=False、data 被吞成 {}，
    餐饮/市内交通静默消失却仍标 coverage=complete；现统一 MAX_DAYS 后 366 天 bad_input。
    """
    ok = compose_itinerary("昆明", ["大理"], "2026-01-01", 365, 10_000_000,
                           client=StubClient())
    assert ok.ok is True
    assert "餐饮（估算）" in {i.name for i in ok.data.budget.items}   # 边界内餐饮入预算
    over = compose_itinerary("昆明", ["大理"], "2026-01-01", 366, 10_000_000,
                             client=StubClient())
    assert over.ok is False and over.error.code == "bad_input"
    assert "duration_days" in over.error.message


def test_researcher_count_upper_bound_aligned_with_living_cost():
    """N08：人数上限与生活成本子能力对齐——20 人可算，21 人整体拒绝。"""
    ok = compose_itinerary("昆明", ["大理"], "2026-01-01", 10, 10_000_000, 20,
                           client=StubClient())
    assert ok.ok is True
    assert "餐饮（估算）" in {i.name for i in ok.data.budget.items}
    over = compose_itinerary("昆明", ["大理"], "2026-01-01", 10, 10_000_000, 21,
                             client=StubClient())
    assert over.ok is False and over.error.code == "bad_input"
    assert "researcher_count" in over.error.message


def test_living_cost_failure_marks_budget_gap_not_zero(monkeypatch):
    """N08 防御兜底：生活成本子能力失败 → 餐饮/市内交通显式进 uncovered、
    coverage 降级、degraded 说明原因，绝不当零费用谎报 complete。"""
    def _fail(**_kw):
        return Envelope(ok=False, error=ToolError(
            code="invalid_arguments", message="days 必须在 1-365 之间，收到 999", hint=""))
    monkeypatch.setattr(itinerary_module, "estimate_living_cost", _fail)
    env = compose_itinerary("昆明", ["大理"], "2026-01-01", 10, 20000, client=StubClient())
    b = env.data.budget
    assert "餐饮（估算）" in b.uncovered
    assert "市内交通（估算）" in b.uncovered
    assert b.coverage == "partial"          # 交通/住宿有价 → partial，而非 complete
    assert b.over_budget is False           # partial 下不得宣布预算内/超支
    assert env.degraded and "生活成本" in env.degraded


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
    assert by_name["餐饮（估算）"] == 18.0 * 3 * 10            # village+mixed 下沿×人数×天数


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
    assert "amap_client" not in props  # N2 接驳注入参数同样不进 schema
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


# ────────────────────────────────────────────────────────────────
# N2 接驳腿（PoC2 §3.2）：amap_client 注入 + 无 Key 短路 + 预算联动
# ────────────────────────────────────────────────────────────────


class StubAmap:
    """N2 接驳替身：geocode/direction_transit 记录调用，可注入三态
    （成功 / 无 Key 短路 / 未收录 / 抛异常）。"""

    def __init__(self, no_key=False, fail_geocode=False, raise_geocode=False,
                 transit_cheapest=8.0):
        self.no_key = no_key
        self.fail_geocode = fail_geocode
        self.raise_geocode = raise_geocode
        self.transit_cheapest = transit_cheapest
        self.geocode_calls: list[str] = []
        self.transit_calls: list[dict] = []

    def geocode(self, address, city=""):
        self.geocode_calls.append(address)
        if self.no_key:
            return Envelope(ok=False, error=ToolError(
                code="amap_invalid_key", message="未配置高德 API Key", hint=""))
        if self.raise_geocode:
            raise RuntimeError("amap subprocess boom")
        if self.fail_geocode:
            return Envelope(ok=False, error=ToolError(
                code="amap_no_result", message="高德未收录该地点", hint=""))
        return Envelope(ok=True, data=GeoLocation(
            location=f"100.{len(address)},25.6", city=address))

    def direction_transit(self, origin, destination, city="", cityd="", date=""):
        self.transit_calls.append({"origin": origin, "destination": destination,
                                   "city": city, "cityd": cityd, "date": date})
        return Envelope(ok=True, data=ItemPage(
            items=[SearchItem(name="8路", price=self.transit_cheapest)],
            total=2, cheapest=self.transit_cheapest, dearest=self.transit_cheapest))


def test_last_mile_options_enter_leg_and_budget():
    """有 Key 有方案：last_mile 进腿、transit 跨城参数正确、预算联动 cheapest×人数。"""
    amap = StubAmap()
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000, 3,
                            client=StubClient(), amap_client=amap)
    assert env.ok is True
    lm = env.data.legs[0]["last_mile"]
    assert lm["hub_geo"] is not None and lm["site_geo"] is not None
    assert lm["options"] is not None and lm["options"].cheapest == 8.0
    t = amap.transit_calls[0]  # v3 跨城语义平移：city=出发市、cityd=到达市
    assert t["origin"] == lm["hub_geo"].location
    assert t["destination"] == lm["site_geo"].location
    assert t["city"] == "昆明" and t["cityd"] == "大理"
    by_name = {i.name: i for i in env.data.budget.items}
    ji = by_name["接驳（昆明→大理）"]  # 预算联动：cheapest×人数，口径标注高德来源
    assert ji.amount == 8.0 * 3 and ji.source == "cheapest" and ji.people == 3
    assert "高德" in ji.unit and ji.priced_at


def test_last_mile_no_key_short_circuits_all_legs():
    """无 Key：首次 amap_invalid_key 后全程短路；接驳缺席不进 uncovered（不影响主链路）。"""
    amap = StubAmap(no_key=True)
    env = compose_itinerary("昆明", ["大理", "丽江"], "2026-11-01", 10, 20000,
                            client=StubClient(), amap_client=amap)
    assert env.ok is True
    assert len(amap.geocode_calls) == 1 and amap.transit_calls == []  # 三腿只首调一次
    for leg in env.data.legs:
        lm = leg["last_mile"]
        assert lm["hub_geo"] is None and lm["site_geo"] is None and lm["options"] is None
        assert "Key" in lm["note"]
    assert all("接驳" not in u for u in env.data.budget.uncovered)
    assert env.data.budget.coverage == "complete"  # 核心口径不因增强缺席降级


def test_last_mile_failure_enters_uncovered_not_error():
    """有 Key 但地名未收录：note 落腿、接驳进 uncovered、coverage=partial、ok=True。"""
    amap = StubAmap(fail_geocode=True)
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000,
                            client=StubClient(), amap_client=amap)
    assert env.ok is True
    lm = env.data.legs[0]["last_mile"]
    assert lm["hub_geo"] is None and lm["options"] is None and lm["note"]
    assert "正在开发中" in lm["note"]  # amap_no_result → 境外接驳缺口须明示
    assert any("接驳" in u for u in env.data.budget.uncovered)  # 查过无价与单腿交通同口径
    assert env.data.budget.coverage == "partial"
    assert all(not i.name.startswith("接驳") for i in env.data.budget.items)


def test_last_mile_exception_never_escapes():
    """amap 抛异常折 note 不逃逸（对照 source_exception 隔离惯例），主链路照常。"""
    amap = StubAmap(raise_geocode=True)
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000,
                            client=StubClient(), amap_client=amap)
    assert env.ok is True
    assert env.data.legs[0]["last_mile"]["note"]
    assert env.data.stays[0]["nights"] == 5  # 住宿主链路不受影响


def test_last_mile_geocode_memo_reuses_places():
    """地名级 memo：同地名跨腿复用正算结果——三地名三腿只打 3 次 geocode（非 6 次）。"""
    amap = StubAmap()
    compose_itinerary("昆明", ["大理", "丽江"], "2026-11-01", 10, 20000,
                      client=StubClient(), amap_client=amap)
    assert sorted(amap.geocode_calls) == sorted(["昆明", "大理", "丽江"])
    assert len(amap.geocode_calls) == 3  # 每地名恰一次（去重后）
    assert len(amap.transit_calls) == 3  # 每腿一次 transit 不受 memo 影响


def test_inputs_hash_independent_of_last_mile():
    """接驳是增强非方案输入：inputs_hash 只由六键决定，与 amap 可用性无关。"""
    a = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000,
                          client=StubClient(), amap_client=StubAmap()).data
    b = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000,
                          client=StubClient(), amap_client=StubAmap(no_key=True)).data
    assert a.inputs_hash == b.inputs_hash


# ────────────────────────────────────────────────────────────────
# N5 live ref（PoC2 §4.3 C3）：接驳腿 last_mile 附规格键（只附成功调用）
# ────────────────────────────────────────────────────────────────


def test_last_mile_leg_carries_live_refs():
    """成功路径：leg.last_mile.live_refs = geocode×2 + transit（行程接驳不传
    date → 键尾恒空段）。StubAmap 坐标 = f"100.{len(地名)},25.6"（昆明/大理
    同为双字 → 同坐标），替身怪癖不影响键格式断言。"""
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000,
                            client=StubClient(), amap_client=StubAmap())
    assert env.ok is True
    assert env.data.legs[0]["last_mile"]["live_refs"] == [
        "@live:amap_geocode:昆明",
        "@live:amap_geocode:大理",
        "@live:amap_direction_transit:100.2,25.6>100.2,25.6|",
    ]


def test_last_mile_partial_fail_keeps_successful_geocode_refs():
    """只附成功调用：site 端未收录 → live_refs 只留已成功的 hub geocode 键。"""
    class HalfFailAmap(StubAmap):
        def geocode(self, address, city=""):
            if address == "大理":
                return Envelope(ok=False, error=ToolError(
                    code="amap_no_result", message="高德未收录该地点", hint=""))
            return super().geocode(address, city)

    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 6, 20000,
                            client=StubClient(), amap_client=HalfFailAmap())
    assert env.ok is True
    assert env.data.legs[0]["last_mile"]["live_refs"] == ["@live:amap_geocode:昆明"]


def test_last_mile_no_key_legs_have_empty_live_refs():
    """无 Key 短路：全程无成功 amap 调用 → 各腿 live_refs 恒空表。"""
    env = compose_itinerary("昆明", ["大理", "丽江"], "2026-11-01", 10, 20000,
                            client=StubClient(), amap_client=StubAmap(no_key=True))
    assert env.ok is True
    assert all(leg["last_mile"]["live_refs"] == [] for leg in env.data.legs)
