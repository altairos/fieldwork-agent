"""SSE 事件 pydantic 模型（spec §3.4 六事件 + R4 信封）。"""

from typing import Any

from pydantic import BaseModel, Field


class MessageDelta(BaseModel):
    """流式回复文本增量。"""

    text: str = ""


class ToolStarted(BaseModel):
    """工具调用开始事件。"""

    call_id: str
    tool: str
    args: dict = Field(default_factory=dict)
    purpose: str = ""


class ToolCompleted(BaseModel):
    """工具调用完成事件。"""

    call_id: str
    ok: bool = True
    data: Any = None
    degraded: str | None = None
    # 失败时的机器可读错误三元组（含配额用尽等场景）；旧事件默认无此字段
    error: dict | None = None
    source_usage: dict | None = None


class PhaseTransition(BaseModel):
    """阶段迁移事件（from/to 为 Python 关键字，故用 alias）。"""

    from_phase: str = Field(alias="from")
    to_phase: str = Field(alias="to")
    model_config = {"populate_by_name": True}


class TurnDone(BaseModel):
    """单轮回复完成事件。"""

    reply_id: str = ""
    usage: dict = Field(default_factory=dict)
    # 本轮抽出的快捷选项（[[options]] 标记剥离结果；无标记为空）
    quick_replies: list[str] = Field(default_factory=list)


class ErrorEvent(BaseModel):
    """错误事件（带提示）。"""

    code: str
    message: str
    hint: str = ""


class EventEnvelope(BaseModel):
    """统一信封：turn_id + 单调序号 + 事件名 + payload。"""

    turn_id: str
    seq: int
    event: str
    data: dict = Field(default_factory=dict)
