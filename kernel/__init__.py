"""fieldwork_agent 能力内核（无状态）。

依赖方向单向：kernel 只依赖标准库与 pydantic，不 import core/、cli、mcp、fastapi。

**裸导入即注册**：各能力实现模块在模块级调用 ``registry.register()``，故本文件必须
聚合导入它们——否则宿主只 ``import fieldwork_agent.kernel`` 时注册表为空、
``build_tool_schemas()`` 返回 0，"工具 schema 单一来源"（spec §3.1）在裸导入路径下失效。
聚合落到**模块级**而非子包级：各子包 ``__init__.py`` 只有 docstring，import 子包不会
触发其内模块。guidance / repository 不注册能力，不在聚合之列。
"""
from fieldwork_agent.kernel.aggregation import itinerary, site_profile  # noqa: F401
from fieldwork_agent.kernel.documents import informants as documents_informants  # noqa: F401
from fieldwork_agent.kernel.documents import proposal  # noqa: F401
from fieldwork_agent.kernel.registry import build_tool_schemas
from fieldwork_agent.kernel.registry import capabilities as CAPABILITIES
from fieldwork_agent.kernel.sources import flyai  # noqa: F401

__all__ = ["CAPABILITIES", "build_tool_schemas"]
