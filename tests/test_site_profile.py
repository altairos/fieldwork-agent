"""profile_field_site 聚合能力测试 — 全部基于 mock，不联网、不调 subprocess。"""
from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.aggregation import site_profile as site_profile_module
from fieldwork_agent.kernel.aggregation.site_profile import profile_field_site
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem


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
