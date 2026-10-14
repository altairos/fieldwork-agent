"""SSE 编码/心跳/Last-Event-ID 解析/StreamingResponse 工厂。"""

import json
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse
from fieldwork_agent.web_host.events import EventEnvelope

MEDIA_TYPE = "text/event-stream"
HEARTBEAT_INTERVAL = 15


def encode_event(envelope: EventEnvelope) -> str:
    """将事件信封编码为 SSE 帧（id/event/data + 空行分隔）。"""
    payload = {"turn_id": envelope.turn_id, "seq": envelope.seq, **envelope.data}
    data_line = json.dumps(payload, ensure_ascii=False, default=str)
    return f"id: {envelope.seq}\nevent: {envelope.event}\ndata: {data_line}\n\n"


def encode_heartbeat() -> str:
    """SSE 注释心跳，避免代理层空闲断连。"""
    return ": heartbeat\n\n"


def parse_last_event_id(headers: dict) -> int:
    """解析 Last-Event-ID 请求头，非法值一律回落 0（断线重连补齐起点）。"""
    raw = headers.get("last-event-id", "0")
    try:
        return max(0, int(raw))
    except (ValueError, TypeError):
        return 0


def sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    """构造 SSE StreamingResponse（禁用缓存与代理缓冲）。"""
    return StreamingResponse(
        generator,
        media_type=MEDIA_TYPE,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
