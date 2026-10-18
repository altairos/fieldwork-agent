"""kernel 数据契约 — 全内核共享的 pydantic 模型。

约束：
- 无状态：模型只描述数据，不做任何 I/O；
- 全字段可省：每个字段都有默认值（list/dict 用 ``default_factory``），
  便于能力函数按需只填自己产出的部分；
- 价格统一为 float（``price_raw`` 保留 CLI 原始字符串用于追溯）。
"""
from __future__ import annotations

import hashlib
import json
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
    schedule: Schedule | None = None    # 航班/火车深层结构；平铺路径恒 None


class ItemPage(BaseModel):
    """一页搜索结果 + 极值摘要，供预算与节奏判断直接取用。"""

    items: list[SearchItem] = Field(default_factory=list)
    total: int = 0
    cheapest: float | None = None
    dearest: float | None = None


# ---------------------------------------------------------------- 地理空间（高德底座）


class GeoLocation(BaseModel):
    """行政区划与坐标定位（高德 geocode/regeocode 归一化产物）。

    ``location`` 恒为高德坐标串 ``"lng,lat"``；``adcode`` 是后续天气/周边搜索的
    行政区锚点。海外地名高德不收录 → 走 ``amap_no_result`` 回退 FlyAI。
    """

    location: str = ""            # "lng,lat"
    province: str = ""
    city: str = ""
    district: str = ""
    township: str = ""
    adcode: str = ""
    formatted_address: str = ""
    source: str = "amap"


# ---------------------------------------------------------------- 田野点画像


class SummaryHints(BaseModel):
    """从田野点资料中抽取的量化提示（分节计数、住宿/交通最低价、降级建议）。"""

    section_counts: dict[str, int] = Field(default_factory=dict)
    accommodation_nightly_min: float | None = None
    transport_min: float | None = None
    # 审阅意见 R5/无供给二段式：段名 → 人读建议（如"枢纽城市住宿+当地学术接待"）
    hints: dict[str, str] = Field(default_factory=dict)


class FieldSiteProfile(BaseModel):
    """一个候选田野点的完整画像。"""

    location: str = ""
    transport: ItemPage | None = None
    accommodation: ItemPage | None = None
    culture: list[dict[str, Any]] = Field(default_factory=list)
    amenities: ItemPage | None = None
    # 高德 PoC M3（§3.4）：地理底座与周边设施，默认 None 旧数据反序列化兼容
    geo: GeoLocation | None = None        # 行政区划底座；geocode 失败（含海外）为 None
    facilities: ItemPage | None = None    # 周边设施盘点（医疗/警务/金融/物流/集市）
    # PoC2 N5（§4.3 C3）：实际成功的高德调用规格键（@live:amap_*，宿主轮末替换）
    live_refs: list[str] = Field(default_factory=list)
    summary: SummaryHints = Field(default_factory=SummaryHints)
    generated_at: str = ""


# ---------------------------------------------------------------- 行程与预算


class PhaseSegment(BaseModel):
    """行程节奏分段（如前期密集访谈 / 后期整理补充）。"""

    label: str = ""
    days: int = 0
    note: str = ""


class BudgetItem(BaseModel):
    """预算条目（审阅意见 R2）：金额必须携带可复核的计价口径与来源。

    ``source`` 标注金额来源（search / cheapest / estimate / manual）；
    报价类条目应同时填 ``priced_at``（报价时间）与 ``source_ref``（可追溯到
    搜索结果条目的引用）；估算类条目用 ``unit``+``quantity``+``people`` 交代口径。
    """

    name: str = ""
    amount: float = 0.0
    source: str = ""
    currency: str = "CNY"
    unit: str = ""
    quantity: float = 1.0
    people: int = 1
    priced_at: str = ""
    source_ref: str = ""


class Schedule(BaseModel):
    """航班/火车班次结构（深层解析产物；酒店/POI 为 None）。"""

    dep_time: str = ""                  # "08:30"
    arr_time: str = ""                  # "10:05"
    duration_minutes: int | None = None # 全程分钟数（缺省未知）
    stops: int = 0                      # 中转/经停次数（segments-1）
    cabin: str = ""                     # 舱位/席别


class BudgetBreakdown(BaseModel):
    """预算汇总（审阅意见 R2）。

    ``coverage``：``complete`` = 所有腿与住宿报价均已获取，``partial`` = 部分缺失，
    ``unknown`` = 无任何报价。**仅 coverage==complete 时 ``over_budget`` 的结论才有
    "完整方案是否超支"的意义**；否则 total 只是已知小计，宿主不得据此宣布"预算内"。
    ``uncovered`` 列出缺失报价的条目名，供界面展示"预算不完整"原因。
    """

    items: list[BudgetItem] = Field(default_factory=list)
    total: float = 0.0
    budget_total: float = 0.0
    over_budget: bool = False
    coverage: str = "unknown"
    uncovered: list[str] = Field(default_factory=list)


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
    # 审阅意见 R3：产出时的关键输入哈希，宿主用于判断行程是否因输入变更而失效
    inputs_hash: str = ""


# ---------------------------------------------------------------- 时窗分析

# ── 时窗分析（analyze_fieldwork_window；spec §4.1）─────────────────


class FestivalInfo(BaseModel):
    """LLM 抽取的节庆信息（农历→公历换算由 LLM 完成，内核只做区间求交）。"""

    name: str = ""                 # "彝族火把节"
    approx_dates: str = ""         # "农历六月廿四，约7月下旬"（人类可读原文）
    approx_start: str = ""         # ISO 日期 "2027-07-26"
    approx_end: str = ""           # ISO 日期 "2027-07-28"
    relevance: str = ""            # 对研究主题的观察价值


class PriceCurve(BaseModel):
    """多日期采样价格曲线（None = 该采样点失败，曲线缺值）。"""

    dates: list[str] = Field(default_factory=list)
    flight_min: list[float | None] = Field(default_factory=list)   # 该日出发最低价
    hotel_min: list[float | None] = Field(default_factory=list)    # 当晚入住最低价


class RecommendedWindow(BaseModel):
    """推荐时窗（end = start + duration_days - 1）。"""

    start: str = ""
    end: str = ""
    score: float = 0.0             # 0-100，确定性评分
    reasons: list[str] = Field(default_factory=list)   # 模板拼装理由链（含统计数字）
    evidence: dict[str, Any] = Field(default_factory=dict)   # 十键评分明细：price_score/festival_score/budget_score/flight_min/hotel_min/coverage/uncovered/estimate/estimate_known/festivals


class WindowAnalysis(BaseModel):
    """analyze_fieldwork_window 的 data 载荷。"""

    windows: list[RecommendedWindow] = Field(default_factory=list)  # 按 score 降序，≤3
    price_curve: PriceCurve = PriceCurve()
    festivals: list[FestivalInfo] = Field(default_factory=list)     # 可为空（抽取降级）
    budget_feasible: bool | None = None       # 最优窗口预算是否可行
    degraded: str | None = None         # 体验模式/节庆降级/部分采样失败说明


# ---------------------------------------------------------------- 研究上下文


class ResearchContext(BaseModel):
    """研究定位槽位；空字符串表示未填，``extras`` 收野生字段。"""

    discipline: str = ""
    topic: str = ""
    site_status: str = ""
    scale: str = ""
    budget: str = ""
    time_window: str = ""
    departure_city: str = ""
    target_location: str = ""
    travel_date: str = ""
    # 审阅意见 R1：输出用途（global|kaiti|grant|course，缺省视为 global）
    purpose: str = ""
    # 轮 2 方案 B：深挖研究问题（经 update_research_context 写入，计入 A→B 推进；
    # 必须声明为正式槽位——宿主经 model_validate(repo dict) 构造，未声明键会被丢弃）
    research_questions: str = ""
    # 审阅意见 R3：非访谈型研究显式跳过访谈对象硬条件（附理由）
    interviews_not_applicable: bool = False
    interviews_na_reason: str = ""
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
    """提案/纪要文档，markdown 为唯一真源。

    ``requested_mode`` 记录用户所选体例，``mode`` 记录**实际生成**体例（审阅意见 R1）：
    ``global`` 为当前唯一完整实现；``kaiti``/``grant`` 回退 global 时两者不一致，
    文档头部会前置声明能力限制（见 ``kernel.documents.proposal``）。
    ``inputs_hash`` 供宿主做确认/失效判定（审阅意见 R3）。
    """

    markdown: str = ""
    kind: str = "proposal"
    version: str = "draft"
    sections: list[str] = Field(default_factory=list)
    requested_mode: str = "global"
    mode: str = "global"
    inputs_hash: str = ""


class InformantCandidate(BaseModel):
    """访谈对象候选档案（六字段，suggest_informants 产物/示范词条的目标结构）。"""

    type: str = ""
    approach: str = ""
    cautions: str = ""
    question_domains: list[str] = Field(default_factory=list)
    ethics: str = ""
    access_channel: str = ""
    preparation: str = ""


class ConfirmationRecord(BaseModel):
    """用户确认记录（审阅意见 R3）：确认的是"产物+当时的关键输入"二元组。

    ``kind``：site / itinerary / proposal；``subject``：项目内标识（如田野点名）。
    输入变更后用 ``inputs_stale(record.inputs_hash, 当前哈希)`` 判断旧确认是否失效。
    审计二 R09：``dep_hash`` 为五路依赖指纹（confirm_hash.compute_confirm_hash）；
    空串 = legacy 记录（无指纹，失效判定保守视 stale）。
    """

    kind: str = ""
    subject: str = ""
    confirmed_at: str = ""
    inputs_hash: str = ""
    dep_hash: str = ""


def compute_inputs_hash(payload: dict[str, Any]) -> str:
    """关键输入 → sha1 hex（键序无关；仅用于失效判定，不承担安全职责）。"""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def inputs_stale(recorded_hash: str, current_hash: str) -> bool:
    """旧确认记录的输入哈希与当前输入哈希不一致即失效（审阅意见 R3）。"""
    return recorded_hash != current_hash
