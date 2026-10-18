"""能力注册表 — 工具 schema 的单一来源。

MCP Server 与 Web 宿主共用 ``build_tool_schemas()``，保证两侧工具定义不漂移；
能力实现只需 ``register(CapabilityMeta(...))`` 即完成接入。
"""
import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

_REGISTRY: dict[str, "CapabilityMeta"] = {}


@dataclass
class CapabilityMeta:
    """一项能力的元信息：名称、描述、实现函数、参数模型与安全标记。"""

    name: str
    description: str
    fn: Callable
    args_model: type[BaseModel]
    read_only: bool = True
    open_world: bool = False


def register(meta: CapabilityMeta) -> None:
    _REGISTRY[meta.name] = meta


def capabilities() -> dict[str, CapabilityMeta]:
    return dict(_REGISTRY)


def validate_tool_args(meta: CapabilityMeta, args: dict) -> tuple[dict | None, str]:
    """``args_model`` 校验 + LLM 字符串化容器参数的定向救援。

    部分模型（如 qwen）偶发把 list/dict 参数序列化成 JSON 字符串（实测
    interests='["宗族祠堂", …]' 触发 "Input should be a valid list"；
    site_profile/itinerary='{…}' 触发 "Input should be a valid dictionary"）。救援
    只在原本要失败的路径上动手：错误为 valid list/valid dictionary 且字段值能
    json.loads 出对应类型（list/dict）时转换重试，正常路径零影响；救不回仍返回首条错误 msg。
    返回 ``(validated, "")`` 成功，或 ``(None, 首条错误 msg)`` 失败。
    """
    try:
        return meta.args_model.model_validate(args).model_dump(), ""
    except ValidationError as exc:
        fixed = dict(args)
        touched = False
        for err in exc.errors():
            loc = err.get("loc") or ()
            if not loc:
                continue
            msg = str(err.get("msg", ""))
            want_list = "valid list" in msg
            want_dict = "valid dictionary" in msg
            if not (want_list or want_dict):
                continue
            val = fixed.get(loc[0])
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                except ValueError:
                    continue
                if (want_list and isinstance(parsed, list)) or \
                        (want_dict and isinstance(parsed, dict)):
                    fixed[loc[0]] = parsed
                    touched = True
        if touched:
            try:
                return meta.args_model.model_validate(fixed).model_dump(), ""
            except ValidationError as retry_exc:
                exc = retry_exc
        msg = exc.errors()[0].get("msg", "") if exc.errors() else str(exc)
        return None, msg


def build_tool_schemas() -> list[dict]:
    """单一来源：MCP Server(Plan2) 与 Web 宿主(Plan3) 共用。"""
    out = []
    for m in _REGISTRY.values():
        schema = m.args_model.model_json_schema()
        schema.pop("title", None)
        out.append({"type": "function", "function": {
            "name": m.name, "description": m.description,
            "parameters": schema}})
    return out
