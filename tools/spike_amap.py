"""spike：高德地理底座真 Key 冒烟（throwaway，不进 CI，docs/plans amap §7）。

用法：python tools/spike_amap.py  （项目根目录，需已装 fieldwork-agent editable）
前置：AMAP_API_KEY 环境变量或 ~/.fieldwork_agent/config.json 的 amap_api_key
已配置（『Web 服务』类型 Key，https://console.amap.com）。

六项验证（M1–M4 验收 + 画像端到端）：
1. geocode("喜洲镇", city="大理") —— 五级行政区划 + 坐标 + adcode（M1/M3）
2. geocode("昆明") —— 枢纽端正算，为 transit 供 origin 坐标（M4 前置）
3. around_search("卫生院", site 坐标, 5km) —— 周边设施画像（M3）
4. direction_transit 昆明→喜洲（跨城 city/cityd 分传，M1/M4）
5. weather(site adcode, all) —— 4 日预报（M1）
6. plan_last_mile("昆明", "喜洲镇") —— M4 端到端聚合（两端正算 + 接驳方案页）
7. profile_field_site("喜洲镇") —— 画像端到端（flyai 段有无 Key 均不阻塞，
   高德 geocode 成功即可出 geo/facilities 段；M3）

Windows 适配（对照 spike_flyai_window.py 惯例）：汇总与各步 report 直写
utf-8 文件（spike_raw/spike_summary.json），绕过 pwsh 控制台 cp936 显示层
乱码；stdout 亦 reconfigure 为 utf-8。amap 走 Python urllib 内客户端，
无 CLI 子进程问题。raw payload 存档到 untracked 目录（.superpowers/.../
spike_raw/），仅供脱敏 fixture 复核，真实响应原文不进版本库。
"""
import json
import sys
import time
from pathlib import Path

from fieldwork_agent.kernel.aggregation.last_mile import plan_last_mile
from fieldwork_agent.kernel.aggregation.site_profile import profile_field_site
from fieldwork_agent.kernel.config import Config
from fieldwork_agent.kernel.models import GeoLocation, ItemPage
from fieldwork_agent.kernel.sources.amap import AMapClient

RAW_DIR = (Path(__file__).resolve().parents[1] / ".superpowers" / "sdd"
           / "2026-10-18-amap-poc" / "spike_raw")


def _save_raw(name: str, payload: dict) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2,
                   default=lambda o: o.model_dump(mode="json")
                   if hasattr(o, "model_dump") else str(o)),
        encoding="utf-8")


def _emit(name: str, report: dict) -> dict:
    _save_raw(name, report)
    print(json.dumps({name: report}, ensure_ascii=False), flush=True)
    return report


def _fail_report(**extra) -> dict:
    return {"pass": False, **extra}


def _error_fields(env) -> dict:
    err = env.error
    return {"code": err.code, "message": err.message} if err else {"code": "", "message": ""}


def verify_geocode(client: AMapClient, address: str, city: str = "") -> dict:
    """地名 → 五级行政区划 + 坐标；pass = ok 且 location/adcode/province 齐。"""
    env = client.geocode(address, city=city)
    if env.ok and isinstance(env.data, GeoLocation):
        geo = env.data
        return _emit(f"geocode_{address}", {
            "pass": bool(geo.location and geo.adcode and geo.province),
            "address": address, "city_param": city, **geo.model_dump()})
    return _emit(f"geocode_{address}",
                 _fail_report(address=address, city_param=city, error=_error_fields(env)))


def verify_around(client: AMapClient, location: str) -> dict:
    """田野点 5km 内卫生院；pass = ok 且有条目（驻村便利性画像）。"""
    env = client.around_search("卫生院", location, radius=5000)
    if env.ok and isinstance(env.data, ItemPage):
        page = env.data
        return _emit("around_clinic", {
            "pass": page.total > 0, "location": location, "total": page.total,
            "first_names": [i.name for i in page.items[:3]]})
    return _emit("around_clinic", _fail_report(location=location, error=_error_fields(env)))


def verify_transit(client: AMapClient, origin: str, destination: str,
                   city: str, cityd: str) -> dict:
    """跨城公交（city=出发市 / cityd=到达市）；pass = ok 且有方案条目。"""
    env = client.direction_transit(origin, destination, city=city, cityd=cityd)
    if env.ok and isinstance(env.data, ItemPage):
        page = env.data
        return _emit("transit_hub_site", {
            "pass": page.total > 0, "city": city, "cityd": cityd,
            "total": page.total, "cheapest": page.cheapest,
            "first": [{"name": i.name, "price": i.price,
                       "duration_min": i.schedule.duration_minutes if i.schedule else None}
                      for i in page.items[:2]]})
    return _emit("transit_hub_site",
                 _fail_report(city=city, cityd=cityd, error=_error_fields(env)))


def verify_weather(client: AMapClient, adcode: str) -> dict:
    """4 日预报（casts）；pass = ok 且至少 1 日。"""
    env = client.weather(adcode, extensions="all")
    if env.ok and isinstance(env.data, list):
        return _emit("weather_forecast", {
            "pass": len(env.data) > 0, "adcode": adcode, "days": len(env.data),
            "first_day": env.data[0].get("date", "") if env.data else ""})
    return _emit("weather_forecast", _fail_report(adcode=adcode, error=_error_fields(env)))


def verify_last_mile(client: AMapClient) -> dict:
    """M4 端到端：昆明→喜洲镇。pass = ok 且两端正算坐标齐；接驳无方案走降级不判败。"""
    env = plan_last_mile("昆明", "喜洲镇", amap_client=client)
    if env.ok and isinstance(env.data, dict):
        data = env.data
        hub_geo, site_geo = data.get("hub"), data.get("site")
        options = data.get("options")
        return _emit("last_mile", {
            "pass": bool(hub_geo and site_geo
                         and getattr(hub_geo, "location", "")
                         and getattr(site_geo, "location", "")),
            "hub": hub_geo.model_dump() if hub_geo else None,
            "site": site_geo.model_dump() if site_geo else None,
            "options_total": options.total if isinstance(options, ItemPage) else None,
            "degraded": env.degraded})
    return _emit("last_mile", _fail_report(error=_error_fields(env)))


def verify_profile(client: AMapClient) -> dict:
    """画像端到端：pass = ok 且 geo 段建立（高德链路）；facilities/degraded 如实记录。"""
    env = profile_field_site("喜洲镇", amap_client=client)
    if env.ok and env.data is not None:
        data = env.data
        geo = getattr(data, "geo", None)
        facilities = getattr(data, "facilities", None)
        counts = getattr(getattr(data, "summary", None), "section_counts", {}) or {}
        return _emit("profile_field_site", {
            "pass": geo is not None,
            "geo_location": geo.location if geo else None,
            "facilities_total": facilities.total if facilities else None,
            "section_counts": counts, "degraded": env.degraded})
    return _emit("profile_field_site", _fail_report(error=_error_fields(env)))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    config = Config()
    if not config.amap_available:
        print("SKIP: 未配置高德 Key（AMAP_API_KEY 或 config.json amap_api_key）")
        return 2
    client = AMapClient(api_key=config.amap_api_key)
    reports: dict[str, dict] = {}

    site = verify_geocode(client, "喜洲镇", city="大理")
    reports["geocode_site"] = site
    time.sleep(1)
    hub = verify_geocode(client, "昆明")
    reports["geocode_hub"] = hub
    time.sleep(1)

    if site.get("location"):
        reports["around"] = verify_around(client, site["location"])
        time.sleep(1)
        reports["weather"] = verify_weather(client, site.get("adcode") or "532901")
        time.sleep(1)
    else:
        reports["around"] = _fail_report(skip="geocode_site 无坐标，链上验证跳过")
        reports["weather"] = _fail_report(skip="geocode_site 无 adcode，链上验证跳过")

    if site.get("location") and hub.get("location"):
        reports["transit"] = verify_transit(
            client, hub["location"], site["location"],
            city=hub.get("city", ""), cityd=site.get("city", ""))
        time.sleep(1)
    else:
        reports["transit"] = _fail_report(skip="hub/site 坐标缺失，transit 跳过")

    reports["last_mile"] = verify_last_mile(client)
    time.sleep(1)
    reports["profile"] = verify_profile(client)

    _save_raw("spike_summary", reports)
    passed = sum(1 for r in reports.values() if r.get("pass"))
    print(f"\n=== 高德真 Key 冒烟：{passed}/{len(reports)} 通过 ===", flush=True)
    for name, rep in reports.items():
        mark = "PASS" if rep.get("pass") else "FAIL"
        skip = f"（{rep['skip']}）" if rep.get("skip") else ""
        print(f"  [{mark}] {name}{skip}", flush=True)
    print(f"明细与 raw payload 已存档：{RAW_DIR}", flush=True)
    return 0 if passed == len(reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
