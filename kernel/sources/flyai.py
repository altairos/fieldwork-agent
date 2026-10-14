"""flyai CLI 的 8 个原子搜索原语 + 结果归一化。

迁移自旧 ``tools/flyai_tools.py``：flyai 子命令名与参数名逐字段保持一致，
仅方法改名（``search_flight→search_flights``、``keyword_search→search_keyword`` 等）。
组合逻辑（行程规划/住宿策略等）不在本层——内核只提供无状态原子能力。

约定：
- 任何失败都不抛异常，统一返回 ``Envelope(ok=False, error=ToolError(code, message, hint))``；
- ``Envelope.data`` 成功时为 ``ItemPage``（``items`` 为 ``SearchItem`` 列表）。
"""
import json
import subprocess
from typing import Any

from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem, ToolError

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
        """航班搜索 — 跨省/跨国调研交通规划。"""
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
