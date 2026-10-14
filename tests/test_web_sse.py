"""SSE 基础设施 + 事件模型单元测试（Task 2）。"""

import json

from fieldwork_agent.web_host.events import EventEnvelope, MessageDelta, ToolStarted
from fieldwork_agent.web_host.sse import encode_event, encode_heartbeat, parse_last_event_id


def test_encode_event_sse_format():
    env = EventEnvelope(
        turn_id="t1",
        seq=7,
        event="message_delta",
        data=MessageDelta(text="你好").model_dump(),
    )
    frame = encode_event(env)
    assert frame.startswith("id: 7\n")
    assert "event: message_delta\n" in frame
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["turn_id"] == "t1" and payload["seq"] == 7 and payload["text"] == "你好"
    assert frame.endswith("\n\n")


def test_heartbeat_is_comment():
    assert encode_heartbeat() == ": heartbeat\n\n"


def test_parse_last_event_id():
    assert parse_last_event_id({"last-event-id": "42"}) == 42
    assert parse_last_event_id({}) == 0
    assert parse_last_event_id({"last-event-id": "abc"}) == 0


def test_tool_started_payload():
    ev = ToolStarted(
        call_id="c1",
        tool="search_hotels",
        args={"dest_name": "大理"},
        purpose="住宿搜索",
    )
    assert ev.model_dump()["call_id"] == "c1"
