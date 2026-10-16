"""RecordingClient：flyai 调用记录替身（spec §5.1 快照证据链的捕获件）。

纯内存记录、不碰 repository（公理 1）；落盘由 web 宿主轮末消费 ``calls`` 完成。
"""
from fieldwork_agent.kernel.models import Envelope, ItemPage, ToolError
from fieldwork_agent.kernel.sources.flyai import RecordingClient


class FakeClient:
    """不触网的最小替身：两个搜索方法返回固定包络。"""

    def search_flights(self, origin, destination, dep_date="", **kw):
        return Envelope(ok=True, data=ItemPage(items=[], total=0))

    def search_keyword(self, query):
        return Envelope(ok=False, error=ToolError(code="flyai_error",
                                                  message="x", hint=""))


def test_wraps_and_records_kwargs():
    rec = RecordingClient(FakeClient())
    env = rec.search_flights(origin="昆明", destination="西昌", dep_date="2026-07-15")
    assert env.ok is True
    assert len(rec.calls) == 1
    name, sig, recorded = rec.calls[0]
    assert name == "search_flights"
    assert sig["args"] == []
    assert sig["kwargs"] == {"origin": "昆明", "destination": "西昌",
                             "dep_date": "2026-07-15"}
    assert recorded is env  # 同一包络对象透传，调用方拿到真实返回


def test_failed_envelope_recorded_as_is():
    rec = RecordingClient(FakeClient())
    env = rec.search_keyword(query="西昌 节庆")
    assert len(rec.calls) == 1
    assert rec.calls[0][0] == "search_keyword"
    assert rec.calls[0][2] is env and env.ok is False


def test_non_callable_attributes_pass_through():
    fake = FakeClient()
    fake.api_key = "k-1"
    rec = RecordingClient(fake)
    assert rec.api_key == "k-1"   # 非方法属性透传，不产生记录
    assert rec.calls == []
