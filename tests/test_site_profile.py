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
