"""flyai CLI 的 8 个原子搜索原语 + 结果归一化 + 能力注册（schema 单一来源）。

迁移自旧 ``tools/flyai_tools.py``：flyai 子命令名与参数名逐字段保持一致，
仅方法改名（``search_flight→search_flights``、``keyword_search→search_keyword`` 等）。
组合逻辑（行程规划/住宿策略等）不在本层——内核只提供无状态原子能力。

约定：
- 任何失败都不抛异常，统一返回 ``Envelope(ok=False, error=ToolError(code, message, hint))``；
- ``Envelope.data`` 成功时为 ``ItemPage``（``items`` 为 ``SearchItem`` 列表）；
- 8 原语各配 pydantic args_model 并 ``register(CapabilityMeta(...))``——工具 schema
  一律由 ``registry.build_tool_schemas()`` 生成（spec §3.1 单一来源；MCP/Web 共用同一面），
  宿主不再手搓搜索 schema。``fn`` 采用与 ``profile_field_site`` 相同的注入模式：
  keyword-only ``client``，宿主 dispatch 注入自持 client 实例，未注入时自建
  ``FlyAIClient()``（搜索类是开放世界：``read_only=True, open_world=True``，
  对照 spec §2.4 annotations 约定）。
"""
import json
import subprocess
from typing import Any

from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem, ToolError
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from pydantic import BaseModel, Field

# normalize_items 消费的候选键（命中即不进入 extra，避免同一信息两处表达）
_NAME_KEYS = ("name", "title")
_PRICE_KEYS = ("adultPrice", "price")
_PIC_KEYS = ("mainPic", "picUrl")
_JUMP_KEYS = ("jumpUrl", "detailUrl")
_MAPPED_KEYS = frozenset(
    {*_NAME_KEYS, *_PRICE_KEYS, *_PIC_KEYS, *_JUMP_KEYS}
)


def parse_price(raw: str | None) -> float | None:
    """价格字符串归一化为 float 元；剥 ``¥``/千分位/``起``，解析失败返回 None。"""
    if raw is None:
        return None
    text = str(raw).replace("¥", "").replace(",", "").replace("起", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _first(entry: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """按优先级取第一个非空值。"""
    for key in keys:
        value = entry.get(key)
        if value:
            return value
    return None


def normalize_items(raw_list: list[dict]) -> list[SearchItem]:
    """把 flyai 野生字段映射为 SearchItem；未识别的键整体收进 ``extra``。"""
    items: list[SearchItem] = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        raw_price = _first(entry, _PRICE_KEYS)
        items.append(SearchItem(
            name=str(_first(entry, _NAME_KEYS) or ""),
            price=parse_price(raw_price),
            price_raw=raw_price if isinstance(raw_price, str) else None,
            pic_url=_first(entry, _PIC_KEYS),
            jump_url=_first(entry, _JUMP_KEYS),
            extra={k: v for k, v in entry.items() if k not in _MAPPED_KEYS},
        ))
    return items


def _error_envelope(message: str) -> Envelope:
    """把 ``_run_command`` 的错误字符串映射为机器可读错误码。"""
    low = message.lower()
    if "not found" in low:
        code, hint = ("flyai_unavailable", "未找到 flyai CLI，请安装后确认其在 PATH 中可执行")
    elif "timed out" in low:
        code, hint = ("flyai_timeout", "flyai 命令 30 秒超时，可放宽网络或缩小筛选条件后重试")
    elif "parse error" in low:
        code, hint = ("parse_error", "flyai 输出不是合法 JSON，可能版本不匹配或接口已变更")
    else:
        code, hint = ("flyai_error", "flyai 返回失败，可更换关键词或放宽条件后重试")
    return Envelope(ok=False, error=ToolError(code=code, message=message, hint=hint))


def _page_envelope(payload: dict[str, Any]) -> Envelope:
    """成功分支：``data.itemList`` 归一化为 ItemPage，并给出极值摘要。"""
    data = payload.get("data") or {}
    raw_list = data.get("itemList") or []
    if not isinstance(raw_list, list):
        raw_list = []
    items = normalize_items(raw_list)
    prices = [i.price for i in items if i.price is not None]
    return Envelope(ok=True, data=ItemPage(
        items=items,
        total=len(raw_list),
        cheapest=min(prices) if prices else None,
        dearest=max(prices) if prices else None,
    ))


class FlyAIClient:
    """flyai CLI 的 8 个原子搜索工具封装（无状态）。"""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def _run_command(self, args: list) -> dict[str, Any]:
        """执行 flyai 子命令，返回解析后的 JSON；任何异常都折成 ``{status:-1,error}``。"""
        cmd = ["flyai"] + args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"status": -1, "error": result.stderr or "command failed"}
            data = json.loads(result.stdout)
            return data
        except FileNotFoundError:
            return {"status": -1, "error": "flyai CLI not found"}
        except subprocess.TimeoutExpired:
            return {"status": -1, "error": "Command timed out after 30s"}
        except json.JSONDecodeError as e:
            return {"status": -1, "error": f"JSON parse error: {e}"}

    def _execute(self, args: list) -> Envelope:
        """跑一条 flyai 命令并归一化为包络。"""
        payload = self._run_command(args)
        if not isinstance(payload, dict) or payload.get("status", 0) != 0 or "error" in payload:
            message = ""
            if isinstance(payload, dict):
                message = str(payload.get("error") or "flyai 调用失败")
            return _error_envelope(message or "flyai 调用失败")
        return _page_envelope(payload)

    # ══════════════════════════════════════════
    # 大交通
    # ══════════════════════════════════════════

    def search_flights(self, origin: str, destination: str, dep_date: str = "",
                       back_date: str = "", sort_type: str = "2", max_price: str = "") -> Envelope:
        """航班搜索 — 跨省/跨国调研大交通。"""
        args = ["search-flight", "--origin", origin, "--destination", destination]
        if dep_date:
            args.extend(["--dep-date", dep_date])
        if back_date:
            args.extend(["--back-date", back_date])
        if sort_type:
            args.extend(["--sort-type", sort_type])
        if max_price:
            args.extend(["--max-price", max_price])
        return self._execute(args)

    def search_trains(self, origin: str, destination: str, dep_date: str = "",
                      back_date: str = "", seat_class: str = "",
                      journey_type: str = "", sort_type: str = "4") -> Envelope:
        """火车搜索 — 高铁/普速备选方案。"""
        args = ["search-train", "--origin", origin, "--destination", destination]
        if dep_date:
            args.extend(["--dep-date", dep_date])
        if back_date:
            args.extend(["--back-date", back_date])
        if seat_class:
            args.extend(["--seat-class-name", seat_class])
        if journey_type:
            args.extend(["--journey-type", journey_type])
        if sort_type:
            args.extend(["--sort-type", sort_type])
        return self._execute(args)

    # ══════════════════════════════════════════
    # 住宿
    # ══════════════════════════════════════════

    def search_hotels(self, dest_name: str, check_in: str = "", check_out: str = "",
                      hotel_types: str = "", max_price: str = "", sort: str = "price_asc",
                      poi_name: str = "", hotel_stars: str = "") -> Envelope:
        """酒店/民宿搜索 — 按类型、价格、POI 距离筛选住宿。"""
        args = ["search-hotel", "--dest-name", dest_name]
        if check_in:
            args.extend(["--check-in-date", check_in])
        if check_out:
            args.extend(["--check-out-date", check_out])
        if hotel_types:
            args.extend(["--hotel-types", hotel_types])
        if max_price:
            args.extend(["--max-price", max_price])
        if sort:
            args.extend(["--sort", sort])
        if poi_name:
            args.extend(["--poi-name", poi_name])
        if hotel_stars:
            args.extend(["--hotel-stars", hotel_stars])
        return self._execute(args)

    def search_marriott_hotels(self, dest_name: str, check_in: str = "", check_out: str = "",
                               hotel_brands: str = "", max_price: str = "",
                               sort: str = "price_asc") -> Envelope:
        """万豪旗下酒店搜索 — 高端备选。"""
        args = ["search-marriott-hotel", "--dest-name", dest_name]
        if check_in:
            args.extend(["--check-in-date", check_in])
        if check_out:
            args.extend(["--check-out-date", check_out])
        if hotel_brands:
            args.extend(["--hotel-brands", hotel_brands])
        if max_price:
            args.extend(["--max-price", max_price])
        if sort:
            args.extend(["--sort", sort])
        return self._execute(args)

    def search_marriott_packages(self, keyword: str, sort_type: str = "price_asc") -> Envelope:
        """万豪套餐搜索 — 体验产品/接待专家类需求。"""
        args = ["search-marriott-package", "--keyword", keyword]
        if sort_type:
            args.extend(["--sort-type", sort_type])
        return self._execute(args)

    # ══════════════════════════════════════════
    # 地点与文化
    # ══════════════════════════════════════════

    def search_pois(self, city_name: str, keyword: str = "", category: str = "",
                    poi_level: str = "") -> Envelope:
        """POI/景点搜索 — 了解当地文化场所、节庆活动。"""
        args = ["search-poi", "--city-name", city_name]
        if keyword:
            args.extend(["--keyword", keyword])
        if category:
            args.extend(["--category", category])
        if poi_level:
            args.extend(["--poi-level", poi_level])
        return self._execute(args)

    def search_keyword(self, query: str) -> Envelope:
        """关键词搜索 — 广义覆盖（签证/租车/保险/美食等）。"""
        return self._execute(["keyword-search", "--query", query])

    def search_ai(self, query: str) -> Envelope:
        """AI 语义搜索 — 自然语言理解复杂意图（核心能力）。"""
        return self._execute(["ai-search", "--query", query])


# ════════════════════════════════════════════════════════════════════
# 能力注册 —— 8 搜索原语进 registry，恢复 schema 单一来源（spec §3.1）
#
# args_model 字段名与 FlyAIClient 方法形参逐一对齐；宿主 dispatch 用
# ``meta.fn(**validated, client=self.flyai)`` 注入自持 client 实例。
# ════════════════════════════════════════════════════════════════════


def _make_search_fn(method_name: str):
    """生成一个搜索能力的 capability fn：注入 client（宿主自持实例），
    未注入时自建 ``FlyAIClient()``（与 ``profile_field_site`` 同一模式）。

    参数经 ``args_model`` 校验后以 kwargs 透传给 ``FlyAIClient.<method_name>``；
    ``method_name`` 与 capability name、FlyAIClient 方法名三者一致。
    """

    def fn(*, client: FlyAIClient | None = None, **kwargs: Any) -> Envelope:
        target = client if client is not None else FlyAIClient()
        return getattr(target, method_name)(**kwargs)

    fn.__name__ = method_name
    fn.__qualname__ = method_name
    return fn


# 模块级 fn：既作 CapabilityMeta.fn，又供宿主 dispatch 直接引用（可导入符号）
search_flights = _make_search_fn("search_flights")
search_trains = _make_search_fn("search_trains")
search_hotels = _make_search_fn("search_hotels")
search_marriott_hotels = _make_search_fn("search_marriott_hotels")
search_marriott_packages = _make_search_fn("search_marriott_packages")
search_pois = _make_search_fn("search_pois")
search_keyword = _make_search_fn("search_keyword")
search_ai = _make_search_fn("search_ai")


class SearchFlightsArgs(BaseModel):
    """``search_flights`` 入参（与 FlyAIClient.search_flights 形参对齐）。"""

    origin: str = Field(description="出发城市")
    destination: str = Field(description="目的地")
    dep_date: str = Field(default="", description="出发日期 YYYY-MM-DD")
    back_date: str = Field(default="", description="返程日期 YYYY-MM-DD")
    sort_type: str = Field(default="2", description="排序方式")
    max_price: str = Field(default="", description="最高价")


class SearchTrainsArgs(BaseModel):
    """``search_trains`` 入参（与 FlyAIClient.search_trains 形参对齐）。"""

    origin: str = Field(description="出发城市")
    destination: str = Field(description="目的地")
    dep_date: str = Field(default="", description="出发日期 YYYY-MM-DD")
    back_date: str = Field(default="", description="返程日期 YYYY-MM-DD")
    seat_class: str = Field(default="", description="席别")
    journey_type: str = Field(default="", description="行程类型")
    sort_type: str = Field(default="4", description="排序方式")


class SearchHotelsArgs(BaseModel):
    """``search_hotels`` 入参（与 FlyAIClient.search_hotels 形参对齐）。"""

    dest_name: str = Field(description="目的地城市")
    check_in: str = Field(default="", description="入住日期")
    check_out: str = Field(default="", description="退房日期")
    hotel_types: str = Field(default="", description="类型如 民宿,客栈")
    max_price: str = Field(default="", description="每晚最高价")
    sort: str = Field(default="price_asc", description="排序")
    poi_name: str = Field(default="", description="邻近POI")
    hotel_stars: str = Field(default="", description="星级")


class SearchMarriottHotelsArgs(BaseModel):
    """``search_marriott_hotels`` 入参（与 FlyAIClient.search_marriott_hotels 形参对齐）。"""

    dest_name: str = Field(description="目的地")
    check_in: str = Field(default="", description="入住日期")
    check_out: str = Field(default="", description="退房日期")
    hotel_brands: str = Field(default="", description="品牌")
    max_price: str = Field(default="", description="最高价")
    sort: str = Field(default="price_asc", description="排序")


class SearchMarriottPackagesArgs(BaseModel):
    """``search_marriott_packages`` 入参（与 FlyAIClient.search_marriott_packages 形参对齐）。"""

    keyword: str = Field(description="关键词")
    sort_type: str = Field(default="price_asc", description="排序")


class SearchPoisArgs(BaseModel):
    """``search_pois`` 入参（与 FlyAIClient.search_pois 形参对齐）。"""

    city_name: str = Field(description="城市")
    keyword: str = Field(default="", description="关键词")
    category: str = Field(default="", description="分类")
    poi_level: str = Field(default="", description="等级")


class SearchKeywordArgs(BaseModel):
    """``search_keyword`` 入参（与 FlyAIClient.search_keyword 形参对齐）。"""

    query: str = Field(description="查询串")


class SearchAiArgs(BaseModel):
    """``search_ai`` 入参（与 FlyAIClient.search_ai 形参对齐）。"""

    query: str = Field(description="自然语言查询")


# description 写明田野场景语义（spec §2.1①：宿主 LLM 靠 schema+description 选型，
# 替代旧关键词路由）；搜索类只读、开放世界 → read_only=True、open_world=True。
register(CapabilityMeta(
    name="search_flights",
    description="航班搜索：跨省/跨国田野调研的大交通，返回归一化价格/班次条目。",
    fn=search_flights, args_model=SearchFlightsArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="search_trains",
    description="火车搜索：高铁/普速备选，用于中小城市与短途田野点大交通。",
    fn=search_trains, args_model=SearchTrainsArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="search_hotels",
    description="酒店/民宿搜索：按价格、类型（民宿/客栈）、POI 距离筛选驻村调研住宿。",
    fn=search_hotels, args_model=SearchHotelsArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="search_marriott_hotels",
    description="万豪旗下酒店搜索：团队/高端接待备选住宿。",
    fn=search_marriott_hotels, args_model=SearchMarriottHotelsArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="search_marriott_packages",
    description="万豪套餐搜索：体验产品/接待专家类组合需求。",
    fn=search_marriott_packages, args_model=SearchMarriottPackagesArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="search_pois",
    description="POI/景点搜索：文化场所、节庆活动、宗教/古迹等兴趣点，服务田野点文化画像。",
    fn=search_pois, args_model=SearchPoisArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="search_keyword",
    description="关键词搜索：签证/租车/保险/美食等广义覆盖，补足结构化搜索触达不到的信息。",
    fn=search_keyword, args_model=SearchKeywordArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="search_ai",
    description="AI 语义搜索：以自然语言理解复杂田野意图（如『适合长期驻村的便宜住宿』），"
                "是处理模糊/长尾需求的核心能力。",
    fn=search_ai, args_model=SearchAiArgs,
    read_only=True, open_world=True))
