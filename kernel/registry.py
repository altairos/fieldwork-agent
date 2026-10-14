"""能力注册表 — 工具 schema 的单一来源。

MCP Server 与 Web 宿主共用 ``build_tool_schemas()``，保证两侧工具定义不漂移；
能力实现只需 ``register(CapabilityMeta(...))`` 即完成接入。
"""
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

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
