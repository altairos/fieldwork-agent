from fieldwork_agent.kernel.aggregation.site_profile import profile_field_site
from fieldwork_agent.kernel.aggregation.spatial import SpatialQuery, execute_matrix
from fieldwork_agent.kernel.models import Envelope, GeoLocation, ItemPage, SearchItem, ToolError


def row(**kwargs):
    return SpatialQuery(label="生产场景", research_purpose="了解日常生产与学习", keywords=["作坊", "合作社"],
        expected_evidence="候选观察场所，进入条件待核实", **kwargs)


class Spatial:
    def __init__(self):
        self.calls = []

    def around_search(self, **kw):
        self.calls.append(kw)
        return Envelope(ok=True, data=ItemPage(items=[SearchItem(name="甲作坊", extra={"id": "a", "adcode": "530102"}),
            SearchItem(name="乙作坊", extra={"id": "b", "adcode": "530102"})]))


def test_enough_candidates_stop_before_synonym_or_radius_expansion():
    client = Spatial()
    reports = execute_matrix([row(expanded_radius_meters=10000)], GeoLocation(location="102,25", adcode="530102"), "甲县", client)
    assert len(client.calls) == 1
    assert reports[0]["status"] == "enough"
    assert reports[0]["items"][0]["extra"]["research_purpose"] == "了解日常生产与学习"


def test_ambiguous_anchor_does_not_search_wrong_place():
    client = Spatial()
    reports = execute_matrix([row()], GeoLocation(location="102,25", ambiguous=True), "同名村", client)
    assert not client.calls and reports[0]["status"] == "unknown"


def test_budget_exhaustion_preserves_partial_candidates_and_stops():
    class Limited(Spatial):
        def around_search(self, **kw):
            self.calls.append(kw)
            return Envelope(ok=False, error=ToolError(code="source_budget_exceeded")) if len(self.calls) > 1 else Envelope(ok=True, data=ItemPage(items=[SearchItem(name="甲作坊", extra={"id": "a"})]))
    client = Limited()
    reports = execute_matrix([row()], GeoLocation(location="102,25", adcode="530102"), "甲县", client)
    assert reports[0]["status"] == "budget_exhausted" and len(reports[0]["items"]) == 1


def test_matrix_profile_skips_generic_tourism_product_queries():
    class Sources(Spatial):
        bad_calls = []
        def search_hotels(self, **kw):
            return Envelope(ok=True, data=ItemPage(items=[]))
        def search_ai(self, *a, **kw):
            self.bad_calls.append((a, kw))
            raise AssertionError("should not issue generic product query")
        search_keyword = search_ai
        search_pois = search_ai
        def geocode(self, **kw):
            return Envelope(ok=True, data=GeoLocation(location="102,25", adcode="530102"))
    c = Sources()
    env = profile_field_site("甲县", query_matrix=[row().model_dump()], facility_keywords=[], client=c, amap_client=c)
    assert env.ok and env.data.spatial_results[0]["status"] == "enough"
    assert len(c.calls) == 1
    assert c.bad_calls == []
