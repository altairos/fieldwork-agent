"""amap live ref 规格键（PoC2 §4.3 C3）。

聚合产出（``plan_last_mile`` 的 data、画像 ``FieldSiteProfile.live_refs``、
行程 ``leg["last_mile"]``）对实际成功的高德调用附 ``@live:amap_*:参数``
规格键，宿主轮末既有替换机制据此把快照写进 search_cache（跨会话可复读，
零重复调用）。与 flyai 原语口径一致：原语层不附键，只由聚合层附；只附
实际成功的调用（失败调用宿主不登记快照，附了会残留原样永远替换不掉）。

键格式（宿主 ``_live_key`` amap 分支须与此逐字符一致）：

- geocode：``@live:amap_geocode:{address}``（city 副参不进键——只影响
  解析优先级，不影响坐标语义）；
- around_search：``@live:amap_around_search:{keywords}|{location}``（radius
  是截断口径非查询语义，不进键）；
- direction_transit：``@live:amap_direction_transit:{origin}>{destination}|{date}``
  （行程接驳不传 date → 尾段空位，与实际调用参数一致，宿主按同参数对账）。
"""


def live_geocode_ref(address: str) -> str:
    """geocode 规格键：只依赖地名。"""
    return f"@live:amap_geocode:{address}"


def live_around_ref(keywords: str, location: str) -> str:
    """around_search 规格键：关键词 + 坐标锚点（radius 不进键）。"""
    return f"@live:amap_around_search:{keywords}|{location}"


def live_transit_ref(origin: str, destination: str, date: str = "") -> str:
    """direction_transit 规格键：起终点坐标 + 班次日期（空串留空段）。"""
    return f"@live:amap_direction_transit:{origin}>{destination}|{date}"
