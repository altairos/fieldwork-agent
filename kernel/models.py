"""kernel 数据契约 — 全内核共享的 pydantic 模型。

约束：
- 无状态：模型只描述数据，不做任何 I/O；
- 全字段可省：每个字段都有默认值（list/dict 用 ``default_factory``），
  便于能力函数按需只填自己产出的部分；
- 价格统一为 float（``price_raw`` 保留 CLI 原始字符串用于追溯）。
"""
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 统一返回包络


class ToolError(BaseModel):
    """错误三元组：机器可读 code + 人读 message + 可选 hint（下一步建议）。"""

    code: str = ""
    message: str = ""
    hint: str = ""


class Envelope(BaseModel):
    """所有能力函数的统一返回包络。"""

    ok: bool = False
    data: Any = None
    error: ToolError | None = None
    degraded: str | None = None


# ---------------------------------------------------------------- 检索与行情


class SearchItem(BaseModel):
    """单条搜索结果（酒店/机票/门票等），价格已归一化为 float 元。"""

    name: str = ""
    price: float | None = None
    price_raw: str | None = None
    jump_url: str | None = None
    pic_url: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ItemPage(BaseModel):
    """一页搜索结果 + 极值摘要，供预算与节奏判断直接取用。"""

    items: list[SearchItem] = Field(default_factory=list)
    total: int = 0
    cheapest: float | None = None
    dearest: float | None = None


# ---------------------------------------------------------------- 田野点画像


class SummaryHints(BaseModel):
    """从田野点资料中抽取的量化提示（分节计数、住宿/交通最低价）。"""

    section_counts: dict[str, int] = Field(default_factory=dict)
    accommodation_nightly_min: float | None = None
    transport_min: float | None = None


class FieldSiteProfile(BaseModel):
    """一个候选田野点的完整画像。"""

    location: str = ""
    transport: ItemPage | None = None
    accommodation: ItemPage | None = None
    culture: list[dict[str, Any]] = Field(default_factory=list)
    amenities: ItemPage | None = None
    summary: SummaryHints = Field(default_factory=SummaryHints)
    generated_at: str = ""


# ---------------------------------------------------------------- 行程与预算


class PhaseSegment(BaseModel):
    """行程节奏分段（如前期密集访谈 / 后期整理补充）。"""

    label: str = ""
    days: int = 0
    note: str = ""


class BudgetItem(BaseModel):
    """预算条目；``source`` 标注金额来源（search / cheapest / manual）以便复核。"""

    name: str = ""
    amount: float = 0.0
    source: str = ""


class BudgetBreakdown(BaseModel):
    """预算汇总与是否超支。"""

    items: list[BudgetItem] = Field(default_factory=list)
    total: float = 0.0
    budget_total: float = 0.0
    over_budget: bool = False


class ItineraryPlan(BaseModel):
    """多目的地行程方案。"""

    departure: str = ""
    destinations: list[str] = Field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    legs: list[dict[str, Any]] = Field(default_factory=list)
    stays: list[dict[str, Any]] = Field(default_factory=list)
    rhythm: list[PhaseSegment] = Field(default_factory=list)
    buffer_days: int = 0
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    budget: BudgetBreakdown = Field(default_factory=BudgetBreakdown)
    generated_at: str = ""


# ---------------------------------------------------------------- 研究上下文


class ResearchContext(BaseModel):
    """研究定位十槽位；空字符串表示未填，``extras`` 收野生字段。"""

    discipline: str = ""
    topic: str = ""
    site_status: str = ""
    scale: str = ""
    budget: str = ""
    time_window: str = ""
    departure_city: str = ""
    target_location: str = ""
    travel_date: str = ""
    extras: dict[str, Any] = Field(default_factory=dict)


class AdvanceResult(BaseModel):
    """阶段流转检查结果。"""

    can_advance: bool = False
    to_phase: str | None = None
    missing_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 记录与文档


class FieldSiteRecord(BaseModel):
    """落盘的田野点记录；``profile`` 存 FieldSiteProfile 的 dict 形式。"""

    name: str = ""
    confirmed: bool = False
    profile: dict[str, Any] | None = None


class InformantRecord(BaseModel):
    """报道人（访谈对象）档案。"""

    code: str = ""
    type: str = ""
    relation: str = ""
    contact: str = ""
    notes: str = ""


class ProposalDoc(BaseModel):
    """提案/纪要文档，markdown 为唯一真源。"""

    markdown: str = ""
    kind: str = "proposal"
    version: str = "draft"
    sections: list[str] = Field(default_factory=list)
