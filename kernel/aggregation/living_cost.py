"""生活成本区间估算 — ``estimate_living_cost`` 能力（表驱动，无 I/O、无 LLM）。

生产会话曾把驻村餐饮按 ¥100–150/人·天估算（城市差旅口径），较云南农村实际高
4–8 倍。本模块用「聚落类型 × 就餐方式」的静态区间表给出可解释估算，金额一律
区间而非单点，并随金额返回口径（caliber）与依据（basis）。

表值为西部农村/县域基线：云南农村居民人均食品烟酒 5,351 元/年（≈14.7 元/人·天，
自炊口径）、人均消费支出 16,214 元/年（≈44.4 元/人·天，含居住）。村内自炊/搭伙
按此上浮取值，城市外食含游客溢价。表值不是实时行情。
"""

from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from pydantic import BaseModel, Field

SETTLEMENT_TYPES = ("village", "town", "county", "city")
BOARD_MODES = ("self_cook", "shared_meal", "mixed", "eat_out")

# 输入域上限（N08）：与调用方 compose_itinerary 共享同一常量，杜绝「行程接受
# 任意正天数/人数、子能力却只收 1-365/1-20」的范围错配。餐饮/市内交通按
# 人均日区间线性外推，上限仅为防御性护栏（非算法本质限制）。
MAX_DAYS = 365
MAX_PEOPLE = 20

_SETTLEMENT_LABELS = {
    "village": "村内", "town": "镇上", "county": "县城", "city": "城市",
}
_BOARD_LABELS = {
    "self_cook": "自炊", "shared_meal": "搭伙", "mixed": "混合", "eat_out": "外食",
}

# 餐饮区间（元/人·天）：FOOD_RANGE[聚落][就餐方式] = (下沿, 上沿)
FOOD_RANGE: dict[str, dict[str, tuple[float, float]]] = {
    "village": {"self_cook": (12.0, 20.0), "shared_meal": (15.0, 25.0),
                "mixed": (18.0, 30.0), "eat_out": (25.0, 45.0)},
    "town": {"self_cook": (15.0, 25.0), "shared_meal": (18.0, 28.0),
             "mixed": (22.0, 38.0), "eat_out": (30.0, 50.0)},
    "county": {"self_cook": (18.0, 30.0), "shared_meal": (22.0, 35.0),
               "mixed": (28.0, 45.0), "eat_out": (35.0, 60.0)},
    "city": {"self_cook": (25.0, 40.0), "shared_meal": (28.0, 45.0),
             "mixed": (35.0, 60.0), "eat_out": (50.0, 90.0)},
}

# 市内交通（元/人·天）：村内步行/电动车为主，进城班车单列在大交通
TRANSPORT_RANGE: dict[str, tuple[float, float]] = {
    "village": (0.0, 5.0), "town": (3.0, 10.0),
    "county": (5.0, 15.0), "city": (8.0, 25.0),
}

BASIS = ("云南农村居民人均食品烟酒 5,351 元/年（≈14.7 元/人·天，自炊口径）、"
         "人均消费支出 16,214 元/年（≈44.4 元/人·天，含居住）；村内自炊/搭伙按此"
         "上浮取值，城市外食含游客溢价。表值为静态基线，不是当地实时行情。")

_ASSUMPTIONS = {
    "self_cook": "自炊按在家做饭、不含酒水",
    "shared_meal": "搭伙按与房东/农户搭伙、不含酒水",
    "mixed": "混合按部分自炊、部分在外解决",
    "eat_out": "外食按当地小馆/食堂，不含宴请",
}


class EstimateLivingCostArgs(BaseModel):
    """``estimate_living_cost`` 入参（description 即模型选值指引）。"""

    settlement_type: str = Field(
        default="village",
        description="聚落类型：village 村 / town 镇 / county 县城 / city 城市；驻村调研通常为 village")
    board_mode: str = Field(
        default="mixed",
        description="就餐方式：self_cook 自炊 / shared_meal 搭伙 / mixed 混合 / eat_out 外食；"
                    "能做饭选 self_cook，与农户搭伙选 shared_meal")
    days: int = Field(description=f"驻留天数（1-{MAX_DAYS}）")
    people: int = Field(default=1, description=f"人数（1-{MAX_PEOPLE}）")
    include_local_transport: bool = Field(
        default=True, description="是否同时估算市内交通（村内日常通勤/短途）")


def _invalid(message: str) -> Envelope:
    return Envelope(ok=False, error=ToolError(
        code="invalid_arguments", message=message,
        hint=("settlement_type 取 " + "/".join(SETTLEMENT_TYPES)
              + "；board_mode 取 " + "/".join(BOARD_MODES)
              + f"；days 取 1-{MAX_DAYS}；people 取 1-{MAX_PEOPLE}")))


def estimate_living_cost(settlement_type: str = "village", board_mode: str = "mixed",
                         days: int = 0, people: int = 1,
                         include_local_transport: bool = True) -> Envelope:
    """按「聚落类型 × 就餐方式」估算餐饮与市内交通区间（元）。"""
    if settlement_type not in SETTLEMENT_TYPES:
        return _invalid(f"未知聚落类型：{settlement_type}")
    if board_mode not in BOARD_MODES:
        return _invalid(f"未知就餐方式：{board_mode}")
    if not 1 <= days <= MAX_DAYS:
        return _invalid(f"days 必须在 1-{MAX_DAYS} 之间，收到 {days}")
    if not 1 <= people <= MAX_PEOPLE:
        return _invalid(f"people 必须在 1-{MAX_PEOPLE} 之间，收到 {people}")

    food_low, food_high = FOOD_RANGE[settlement_type][board_mode]
    items = [{
        "name": "餐饮",
        "per_day_low": food_low, "per_day_high": food_high,
        "amount_low": round(food_low * people * days, 2),
        "amount_high": round(food_high * people * days, 2),
        "unit": "元/人·天×人数×天数",
    }]
    assumptions = [_ASSUMPTIONS[board_mode]]
    if include_local_transport:
        tr_low, tr_high = TRANSPORT_RANGE[settlement_type]
        items.append({
            "name": "市内交通",
            "per_day_low": tr_low, "per_day_high": tr_high,
            "amount_low": round(tr_low * people * days, 2),
            "amount_high": round(tr_high * people * days, 2),
            "unit": "元/人·天×人数×天数",
        })
        assumptions.append("市内交通不含城际往返（大交通另计）")

    return Envelope(ok=True, data={
        "settlement_type": settlement_type,
        "board_mode": board_mode,
        "people": people,
        "days": days,
        "caliber": (f"{_SETTLEMENT_LABELS[settlement_type]}"
                    f"{_BOARD_LABELS[board_mode]} · {people} 人 × {days} 天"),
        "items": items,
        "total_low": round(sum(i["amount_low"] for i in items), 2),
        "total_high": round(sum(i["amount_high"] for i in items), 2),
        "assumptions": assumptions,
        "basis": BASIS,
        "source": "estimate",
        "coverage": "estimate",
    })


register(CapabilityMeta(
    name="estimate_living_cost",
    description="生活成本区间估算（餐饮/市内交通）：按聚落类型 × 就餐方式查表，"
                "返回区间与口径，不返回单点精确值。驻村场景用 village + "
                "self_cook/shared_meal；不得把城市游客标准套到村里。",
    fn=estimate_living_cost,
    args_model=EstimateLivingCostArgs,
    read_only=True,
))
