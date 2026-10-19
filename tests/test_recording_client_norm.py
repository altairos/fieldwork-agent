"""RecordingClient 归一化视图（spec C5，R17）：直通与聚合等效查询同键的前提。"""
import contextvars

from fieldwork_agent.kernel.models import Envelope
from fieldwork_agent.kernel.sources.flyai import RecordingClient, current_call_id


class _Inner:
    def search_flights(self, origin, destination, dep_date="", sort_type="2"):
        return Envelope(ok=True, data=None)

    def text_search(self, keywords, city="", types=""):
        return Envelope(ok=True, data=None)


def _rec():
    return RecordingClient(_Inner())


def test_positional_and_kwargs_normalize_to_same_view():
    rec = _rec()
    rec.search_flights("昆明", "北京", dep_date="2026-11-01")          # 位置 + 省默认
    rec.search_flights(origin="昆明", destination="北京",
                       dep_date="2026-11-01", sort_type="2")            # 全 kwargs
    v1, v2 = rec.calls[0][1]["view"], rec.calls[1][1]["view"]
    assert v1 == v2 == {"origin": "昆明", "destination": "北京",
                        "dep_date": "2026-11-01", "sort_type": "2"}


def test_call_id_captured_from_contextvar():
    rec = _rec()
    token = current_call_id.set("call_42")
    try:
        rec.text_search(keywords="客栈", city="大理")
    finally:
        current_call_id.reset(token)
    assert rec.calls[0][1]["call_id"] == "call_42"
    assert rec.calls[0][1]["view"] == {"keywords": "客栈", "city": "大理", "types": ""}


def test_default_call_id_empty_and_legacy_keys_kept():
    rec = _rec()
    rec.text_search("客栈")
    sig = rec.calls[0][1]
    assert sig["call_id"] == ""
    assert sig["args"] == ["客栈"] and sig["kwargs"] == {}   # 既有键不破坏


def test_contextvar_isolated_across_contexts():
    """并行工具调用互不串号：copy_context 内 set 只影响该上下文。"""
    rec = _rec()
    def _in_ctx(cid):
        current_call_id.set(cid)
        rec.text_search(keywords=cid)
    ctx1 = contextvars.copy_context()
    ctx1.run(_in_ctx, "a")
    # 主上下文未置位 → call_id 仍为空
    rec.text_search(keywords="main")
    assert rec.calls[0][1]["call_id"] == "a"
    assert rec.calls[1][1]["call_id"] == ""
