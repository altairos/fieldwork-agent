"""profile_field_site 聚合能力测试 — 全部基于 mock，不联网、不调 subprocess。"""
from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.aggregation import site_profile as site_profile_module
from fieldwork_agent.kernel.aggregation.site_profile import profile_field_site
from fieldwork_agent.kernel.models import (
    Envelope,
    GeoLocation,
    ItemPage,
    SearchItem,
    ToolError,
)


class StubClient:
    def __init__(self): self.calls = []
    def _page(self, items): return Envelope(ok=True, data=ItemPage(items=items, total=len(items),
                                            cheapest=None, dearest=None))
    def search_hotels(self, dest_name, **kw):
        self.calls.append(("hotel", kw.get("hotel_types", "")))
        return self._page([SearchItem(name="M", price=180.0)]) if "民宿" in kw.get("hotel_types","") else Envelope(ok=False)
    def search_pois(self, city_name, **kw):
        self.calls.append(("poi", kw.get("category","")))
        return self._page([SearchItem(name="祠堂")])
    def search_ai(self, query):
        self.calls.append(("ai", query))
        return Envelope(ok=False)
    def search_keyword(self, query): return self._page([SearchItem(name="节庆活动")])
    def search_flights(self, **kw): return self._page([SearchItem(name="MU5843", price=620.0)])
    def search_trains(self, **kw): return Envelope(ok=False)


def test_profile_sections_and_failure_isolation():
    c = StubClient()
    env = profile_field_site("大理", client=c, departure_city="昆明", travel_date="2026-11-01")
    p = env.data
    assert p.accommodation.items[0].name == "M"          # 民宿段成功
    assert p.transport is not None                        # 航班成功、火车失败但段不缺
    assert p.culture and p.amenities
    # budget筛选：民宿价180 ≤ 200 保留


def test_budget_filter_drops_over():
    class Over(StubClient):
        def search_hotels(self, dest_name, **kw):
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name="贵宿", price=350.0)], total=1))
    env = profile_field_site("大理", budget_per_night=200, client=Over())
    assert len(env.data.accommodation.items) == 0  # 350 超上浮线(200*1.5=300)被剔除


# ────────────────────────────────────────────────────────────────
# 以下为补充测试：全源失败 / 异常隔离 / SummaryHints / 注册面 / 默认值
# ────────────────────────────────────────────────────────────────


class AllFailClient:
    """六个原语全部返回 ok=False：触发 all_sources_failed。"""

    def search_hotels(self, dest_name, **kw): return Envelope(ok=False)
    def search_pois(self, city_name, **kw): return Envelope(ok=False)
    def search_ai(self, query): return Envelope(ok=False)
    def search_keyword(self, query): return Envelope(ok=False)
    def search_flights(self, **kw): return Envelope(ok=False)
    def search_trains(self, **kw): return Envelope(ok=False)


def test_all_sources_failed_returns_error_envelope():
    env = profile_field_site("大理", client=AllFailClient(), departure_city="昆明",
                             travel_date="2026-11-01")
    assert env.ok is False
    assert env.error is not None and env.error.code == "all_sources_failed"
    assert env.error.message  # code+message 必带（Task 1 约定）


def test_exception_is_isolated_per_source():
    class Raising(AllFailClient):
        def search_pois(self, city_name, **kw): raise RuntimeError("subprocess boom")
        def search_hotels(self, dest_name, **kw):
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name="M", price=180.0)], total=1))
    env = profile_field_site("大理", client=Raising())
    assert env.ok is True                       # 原语抛异常同样被逐源隔离
    assert env.data.accommodation.items[0].name == "M"
    assert env.data.culture == []               # POI 段异常 → 该段置空


def test_summary_hints_and_truncation():
    class Many(StubClient):
        def search_pois(self, city_name, **kw):
            self.calls.append(("poi", kw.get("category", "")))
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name=f"P{i}") for i in range(10)], total=10))
    env = profile_field_site("大理", client=Many(), departure_city="昆明",
                             travel_date="2026-11-01")
    s = env.data.summary
    assert s.accommodation_nightly_min == 180.0  # 住宿段成功价 min
    assert s.transport_min == 620.0              # 交通段成功价 min（火车失败不贡献）
    assert s.section_counts["culture"] == 5      # 4 兴趣 × 10 条截断到 max_items_per_section
    assert s.section_counts["accommodation"] == 1
    assert env.data.generated_at                 # datetime.now().isoformat()


def test_default_interests_used_when_not_given():
    c = StubClient()
    profile_field_site("大理", client=c)
    cats = sorted(cat for kind, cat in c.calls if kind == "poi")
    assert cats == sorted(["人文古迹", "宗教场所", "古镇古村", "博物馆"])


def test_custom_interests_override_defaults():
    c = StubClient()
    profile_field_site("丽江", interests=["寺庙"], client=c)
    assert ("poi", "寺庙") in c.calls
    assert ("poi", "人文古迹") not in c.calls


def test_registered_in_build_tool_schemas():
    """模块导入时 register() 生效：registry 是 Plan2/3 的接入面。"""
    tool = [t for t in build_tool_schemas() if t["function"]["name"] == "profile_field_site"]
    assert tool, "profile_field_site 应已注册"
    props = tool[0]["function"]["parameters"]["properties"]
    assert "location" in props
    assert "client" not in props  # 注入参数不进 schema
    assert "amap_client" not in props  # M3：高德注入参数同样不进 schema（注入缝约定）
    assert props["budget_per_night"]["default"] == 200
    assert props["max_items_per_section"]["default"] == 5


def test_default_client_self_built(monkeypatch):
    """client=None 时自建 FlyAIClient()（无 API Key 也不联网，测试用替身）。"""
    built = []

    class FakeFactory(AllFailClient):
        def __init__(self, api_key=""):
            built.append(api_key)

    monkeypatch.setattr(site_profile_module, "FlyAIClient", FakeFactory)
    env = profile_field_site("大理")
    assert built == [""]
    assert env.ok is False and env.error.code == "all_sources_failed"


# ────────────────────────────────────────────────────────────────
# 评审补测（Task 5 review-fix）：
#   1) AI 归段 + price=None 保留（site_profile.py:107-108 + :147-151 分支）
#   2) 整段全失败 → 该段为 None（此前仅 pin 了 culture 的 [] 路径）
# ────────────────────────────────────────────────────────────────


def test_ai_results_merged_into_accommodation_and_budget_filter():
    """AI 语义搜索成功结果并入住宿段并参与预算筛选：

    - 两个 ``search_hotels`` 都失败 → 若 accommodation 仍非 None，其条目
      只能来自 AI 调用路径（这是本用例最强的路径证据）；
    - ``budget_per_night=200`` → ``cap=300``：price=None 原样保留、
      price=180 保留、price=350 剔除。
    """
    class AIMerge(StubClient):
        def search_hotels(self, dest_name, **kw):
            self.calls.append(("hotel", kw.get("hotel_types", "")))
            return Envelope(ok=False)  # 两策略都失败，逼出 AI 分支
        def search_ai(self, query):
            self.calls.append(("ai", query))
            return self._page([
                SearchItem(name="AI-招待所无价", price=None),  # 保留
                SearchItem(name="AI-村内民宿", price=180.0),   # 保留（≤300）
                SearchItem(name="AI-贵宿", price=350.0),       # 剔除（>300）
            ])
    c = AIMerge()
    env = profile_field_site("大理", budget_per_night=200, client=c)

    # 路径证据：hotel 两次调用都失败；ai 至少被调用一次
    assert sum(1 for kind, _ in c.calls if kind == "hotel") == 2
    assert sum(1 for kind, _ in c.calls if kind == "ai") == 1

    assert env.ok is True
    acc = env.data.accommodation
    assert acc is not None, "AI 成功 → accommodation 段不应为 None"
    # 条目全部来自 AI 路径（两个 hotel 均失败，by_tag 只可能带 ai 页）
    assert [i.name for i in acc.items] == ["AI-招待所无价", "AI-村内民宿"]
    assert acc.total == 2, "total 记截断前（筛选后）条数：3-1(超线)=2"
    # price=None 项保留不会污染最低价统计：min 只看有价条目
    assert env.data.summary.accommodation_nightly_min == 180.0


def test_accommodation_all_fail_yields_none_not_empty_page():
    """三策略全失败但其他段成功 → accommodation=None（区分于筛选后为空的 ItemPage）。

    此前仅 culture=[] 路径被 ``test_exception_is_isolated_per_source`` pin 住；
    本用例补 pin "整段全失败 → None"，锁死"空页 vs 缺段"的语义差异。
    """
    class AccAllFail(StubClient):
        def search_hotels(self, dest_name, **kw):
            self.calls.append(("hotel", kw.get("hotel_types", "")))
            return Envelope(ok=False)
        def search_ai(self, query):
            self.calls.append(("ai", query))
            return Envelope(ok=False)
    env = profile_field_site("大理", client=AccAllFail(), departure_city="昆明",
                             travel_date="2026-11-01")
    assert env.ok is True
    assert env.data.accommodation is None                 # 整段缺位 → None
    assert env.data.transport is not None                 # 航班成功，失败隔离仍守住
    assert env.data.culture                               # 其他段有内容
    assert env.data.summary.section_counts["accommodation"] == 0


def test_no_supply_remote_hint():
    """R6：海外偏远点无住宿供给→二段式建议（枢纽城市+学术接待），不当成"搜到了但超预算"。"""
    class NoSupplyClient:
        def search_hotels(self, dest_name, **kw):
            return Envelope(ok=True, data=ItemPage(items=[], total=0))
        def search_ai(self, query):
            return Envelope(ok=True, data=ItemPage(items=[], total=0))
        def search_keyword(self, query):
            return Envelope(ok=True, data=ItemPage(items=[], total=0))
        def search_pois(self, city_name, **kw):
            return Envelope(ok=True, data=ItemPage(items=[], total=0))

    env = profile_field_site("芒特哈根", client=NoSupplyClient())
    assert env.ok
    assert "枢纽" in env.data.summary.hints.get("accommodation", "")


# ────────────────────────────────────────────────────────────────
# M3（高德 PoC 设计 §5.1）：geo/facilities 段 — 注入/落位/隔离/预算
# 注入参数名 amap_client（非设计稿字面 geo_client）：与 M2 确立的三宿主
# 高德注入键统一（RESERVED_KEYS/host_ctx/inject 标记），MCP/Web 注入缝零改动。
# ────────────────────────────────────────────────────────────────


class StubGeoClient:
    """AMapClient 替身：geocode 固定喜洲坐标；around 每词两条设施。

    calls 记录 (method, kwargs) 供预算（§5.1 ≤6 次高德请求）与锚点断言。
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def geocode(self, address: str, city: str = ""):
        self.calls.append(("geocode", {"address": address, "city": city}))
        return Envelope(ok=True, data=GeoLocation(
            location="100.196,25.047", province="云南省",
            city="大理白族自治州", district="大理市", adcode="532901"))

    def around_search(self, keywords: str, location: str, radius: int = 5000):
        self.calls.append(("around_search", {"keywords": keywords,
                                             "location": location, "radius": radius}))
        # "银行"与"快递"命中同名条目，钉跨关键词合并去重；其余按词命名
        name = "喜洲邮政所" if keywords in ("银行", "快递") else f"{keywords}点"
        return Envelope(ok=True, data=ItemPage(
            items=[SearchItem(name=name), SearchItem(name=f"{keywords}·备")], total=2))


def test_geo_and_facilities_sections_populated():
    """成功路径：geo 落 GeoLocation，五关键词 around 合并去重为 facilities，
    section_counts 增 geo/facilities；高德请求预算恰 6 次（1 geocode + 5 around）。"""
    geo = StubGeoClient()
    env = profile_field_site("喜洲", client=StubClient(), amap_client=geo)
    assert env.ok
    p = env.data
    assert p.geo is not None and p.geo.adcode == "532901"
    assert p.geo.province == "云南省"
    assert p.facilities is not None
    names = [i.name for i in p.facilities.items]
    assert names.count("喜洲邮政所") == 1        # 跨关键词去重（银行/快递同点只留一份）
    assert p.facilities.total == 9              # 截断前：5 词 × 2 条 − 1 重复
    assert len(p.facilities.items) == 5         # 每段截断 max_items_per_section
    assert p.summary.section_counts["geo"] == 1
    assert p.summary.section_counts["facilities"] == len(p.facilities.items)
    # 预算钉子：§5.1 单次画像 ≤6 次高德请求
    methods = [m for m, _ in geo.calls]
    assert len(geo.calls) == 6
    assert methods.count("geocode") == 1 and methods.count("around_search") == 5
    # around 以 geocode 坐标为锚点、默认半径 5km、覆盖五类设施关键词
    around_kwargs = [kw for m, kw in geo.calls if m == "around_search"]
    assert {kw["keywords"] for kw in around_kwargs} == {
        "卫生院 诊所", "派出所", "银行", "快递", "集贸市场"}
    assert all(kw["location"] == "100.196,25.047" and kw["radius"] == 5000
               for kw in around_kwargs)


def test_geo_failure_degrades_not_fails():
    """geocode 失败（海外地名 amap_no_result 为典型）→ geo/facilities=None、
    不发任何 around 请求、整体 ok=True 不受影响、degraded 附双源回退说明。"""
    class GeoNoResult(StubGeoClient):
        def geocode(self, address: str, city: str = ""):
            self.calls.append(("geocode", {"address": address}))
            return Envelope(ok=False, error=ToolError(
                code="amap_no_result", message="高德未收录该地名"))
    geo = GeoNoResult()
    env = profile_field_site("Chiang Mai", client=StubClient(), amap_client=geo)
    assert env.ok is True                       # 逐段失败隔离：geo 段失败不拖垮画像
    assert env.data.geo is None and env.data.facilities is None
    assert env.data.summary.section_counts["geo"] == 0
    assert env.data.summary.section_counts["facilities"] == 0
    assert env.data.accommodation.items[0].name == "M"   # flyai 段照常
    assert [m for m, _ in geo.calls] == ["geocode"]      # 无锚点不跟发 around
    assert env.degraded and "高德" in env.degraded        # 隐式双源回退说明


def test_geo_exception_is_isolated():
    """geo_client 抛异常与返回失败包络同权：geo/facilities=None 且整体 ok=True。"""
    class GeoBoom(StubGeoClient):
        def geocode(self, address: str, city: str = ""):
            raise RuntimeError("amap subprocess boom")
    env = profile_field_site("大理", client=StubClient(), amap_client=GeoBoom())
    assert env.ok is True
    assert env.data.geo is None and env.data.facilities is None


def test_around_all_fail_yields_facilities_none():
    """geocode 成功但五关键词 around 全失败 → facilities=None（缺段语义，
    区分于合并后为空的 ItemPage）；geo 段不受连坐、不算整体降级。"""
    class AroundFail(StubGeoClient):
        def around_search(self, keywords: str, location: str, radius: int = 5000):
            self.calls.append(("around_search", {"keywords": keywords}))
            return Envelope(ok=False)
    geo = AroundFail()
    env = profile_field_site("大理", client=StubClient(), amap_client=geo)
    assert env.ok is True
    assert env.data.geo is not None             # geo 段独立成功
    assert env.data.facilities is None
    assert env.degraded is None                 # geocode 成功不算降级
    assert len(geo.calls) == 6                  # 预算照常花满：1 geocode + 5 around
