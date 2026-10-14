"""
FlyAI 工具封装 — 飞猪旅行搜索能力
深度集成全部 8 个工具，针对田野调查场景优化
"""
import json
import subprocess
from datetime import datetime
from typing import Optional, List, Dict, Any


class FlyAITools:
    """封装 flyai CLI 的全部 8 个工具"""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def _run_command(self, args: list) -> Dict[str, Any]:
        """执行 flyai 子命令，返回解析后的 JSON"""
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

    # ══════════════════════════════════════════
    # 基础工具（直接映射）
    # ══════════════════════════════════════════

    def search_flight(self, origin: str, destination: str, dep_date: str = "",
                      back_date: str = "", sort_type: str = "2", max_price: str = "") -> Dict[str, Any]:
        """航班搜索 — 用于跨省/跨国调研交通规划"""
        args = ["search-flight", "--origin", origin, "--destination", destination]
        if dep_date:
            args.extend(["--dep-date", dep_date])
        if back_date:
            args.extend(["--back-date", back_date])
        if sort_type:
            args.extend(["--sort-type", sort_type])
        if max_price:
            args.extend(["--max-price", max_price])
        return self._run_command(args)

    def search_train(self, origin: str, destination: str, dep_date: str = "",
                     back_date: str = "", seat_class: str = "",
                     journey_type: str = "", sort_type: str = "4") -> Dict[str, Any]:
        """火车搜索 — 高铁备选方案"""
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
        return self._run_command(args)

    def search_hotel(self, dest_name: str, check_in: str = "", check_out: str = "",
                     hotel_types: str = "", max_price: str = "", sort: str = "price_asc",
                     poi_name: str = "", hotel_stars: str = "") -> Dict[str, Any]:
        """酒店搜索 — 按距离/星级/类型筛选住宿"""
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
        return self._run_command(args)

    def search_poi(self, city_name: str, keyword: str = "", category: str = "",
                   poi_level: str = "") -> Dict[str, Any]:
        """POI/景点搜索 — 了解当地文化场所、节庆活动"""
        args = ["search-poi", "--city-name", city_name]
        if keyword:
            args.extend(["--keyword", keyword])
        if category:
            args.extend(["--category", category])
        if poi_level:
            args.extend(["--poi-level", poi_level])
        return self._run_command(args)

    def keyword_search(self, query: str) -> Dict[str, Any]:
        """关键词搜索 — 广义覆盖（签证/租车/保险/美食等）"""
        return self._run_command(["keyword-search", "--query", query])

    def ai_search(self, query: str) -> Dict[str, Any]:
        """AI语义搜索 — 自然语言理解复杂意图 ← 核心杀手锏"""
        return self._run_command(["ai-search", "--query", query])

    def search_marriott_hotel(self, dest_name: str, check_in: str = "", check_out: str = "",
                              hotel_brands: str = "", max_price: str = "", sort: str = "price_asc") -> Dict[str, Any]:
        """万豪旗下酒店搜索 — 高端备选"""
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
        return self._run_command(args)

    def search_marriott_package(self, keyword: str, sort_type: str = "price_asc") -> Dict[str, Any]:
        """万豪套餐搜索 — 体验产品/接待专家"""
        args = ["search-marriott-package", "--keyword", keyword]
        if sort_type:
            args.extend(["--sort-type", sort_type])
        return self._run_command(args)

    # ══════════════════════════════════════════
    # 田野调查专用组合方法
    # ══════════════════════════════════════════

    def plan_field_site_access(
        self, departure_city: str, target_location: str, travel_date: str,
        budget_per_person: float = 1500
    ) -> Dict[str, Any]:
        """
        为田野点制定完整交通方案
        综合航班+火车+当地交通，输出最优路线
        """
        results = {}

        # 策略1：航班搜索（优先，速度快）
        flight_result = self.search_flight(departure_city, target_location, travel_date, sort_type="3")
        if flight_result.get("status") == 0:
            items = flight_result.get("data", {}).get("itemList", [])
            # 筛选预算内选项
            affordable = []
            for item in items[:10]:
                price_str = item.get("adultPrice", "¥0").replace("¥", "").replace(",", "")
                try:
                    price = float(price_str.split(".")[0])
                    if price <= budget_per_person * 1.5:  # 允许上浮50%
                        affordable.append(item)
                except ValueError:
                    pass
            results["flights"] = {
                "total_found": len(items),
                "affordable_options": affordable[:5],
                "cheapest": items[0] if items else None,
            }

        # 策略2：火车搜索（价格敏感时推荐）
        train_result = self.search_train(departure_city, target_location, travel_date, sort_type="4")
        if train_result.get("status") == 0:
            items = train_result.get("data", {}).get("itemList", [])
            affordable = []
            for item in items[:10]:
                price_str = item.get("adultPrice", "¥0").replace("¥", "").replace(",", "")
                try:
                    price = float(price_str.split(".")[0])
                    if price <= budget_per_person:
                        affordable.append(item)
                except ValueError:
                    pass
            results["trains"] = {
                "total_found": len(items),
                "affordable_options": affordable[:5],
                "cheapest": items[0] if items else None,
            }

        # 策略3：AI语义搜索当地交通概况
        local_transport = self.ai_search(f"{target_location} 交通出行方式 从市区到调研点")
        if local_transport.get("status") == 0:
            results["local_transport"] = local_transport.get("data", {})

        return {
            "departure": departure_city,
            "destination": target_location,
            "date": travel_date,
            "budget_per_person": budget_per_person,
            "routes": results,
            "generated_at": datetime.now().isoformat(),
        }

    def find_accommodation_for_field_work(
        self, location: str, check_in: str = "", check_out: str = "",
        budget_per_night: float = 200, duration_days: int = 7
    ) -> Dict[str, Any]:
        """
        为田野工作查找住宿
        多策略：民宿优先 + 经济型酒店 + POI附近筛选
        """
        results = {}

        # 策略1：民宿搜索（田野调查首选）
        homestay_result = self.search_hotel(location, check_in, check_out,
                                            hotel_types="民宿,客栈", max_price=str(int(budget_per_night)),
                                            sort="price_asc")
        if homestay_result.get("status") == 0:
            results["homestays"] = homestay_result.get("data", {}).get("itemList", [])[:10]

        # 策略2：普通酒店搜索（作为备选）
        hotel_result = self.search_hotel(location, check_in, check_out,
                                         max_price=str(int(budget_per_night * 1.5)), sort="price_asc")
        if hotel_result.get("status") == 0:
            results["hotels"] = hotel_result.get("data", {}).get("itemList", [])[:10]

        # 策略3：AI语义搜索（理解"适合长期驻村"的复杂意图）
        ai_result = self.ai_search(f"{location} 适合长期驻村的便宜住宿 民宿 招待所")
        if ai_result.get("status") == 0:
            results["ai_suggested"] = ai_result.get("data", {})

        # 计算总成本估算
        total_budget = budget_per_night * duration_days
        results["budget_estimate"] = {
            "per_night": budget_per_night,
            "duration_days": duration_days,
            "total": total_budget,
        }

        return {
            "location": location,
            "duration_days": duration_days,
            "strategies_used": list(results.keys()),
            "results": results,
            "generated_at": datetime.now().isoformat(),
        }

    def analyze_field_site_culture_and_attractions(
        self, city_or_region: str, categories_of_interest: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        分析田野点的文化和景点资源
        支持按分类检索宗教场所、古镇古村、博物馆等
        帮助研究者了解当地文化氛围和潜在调研机会
        """
        results = {}

        # 默认关注：人文古迹 + 宗教场所 + 古镇古村 + 博物馆
        default_categories = ["人文古迹", "宗教场所", "古镇古村", "博物馆", "历史古迹"]
        categories_to_search = categories_of_interest or default_categories

        for category in categories_to_search:
            poi_result = self.search_poi(city_or_region, category=category, poi_level="4")
            if poi_result.get("status") == 0:
                items = poi_result.get("data", {}).get("itemList", [])
                results[f"poi_{category}"] = {
                    "category": category,
                    "total_found": len(items),
                    "top_items": items[:5],
                }

        # 广义搜索当地文化活动和节庆
        culture_kw = self.keyword_search(f"{city_or_region} 文化活动 节庆 民俗")
        if culture_kw.get("status") == 0:
            results["cultural_events"] = culture_kw.get("data", {}).get("itemList", [])[:10]

        return {
            "region": city_or_region,
            "categories_searched": categories_to_search,
            "results": results,
            "generated_at": datetime.now().isoformat(),
        }

    def comprehensive_travel_plan(
        self, departure: str, destinations: List[str], start_date: str,
        duration_days: int, budget_total: float = 20000,
        researcher_count: int = 1
    ) -> Dict[str, Any]:
        """
        综合旅行计划 — 整合所有 8 个工具的能力
        生成完整的田野调查旅行方案
        """
        plan = {
            "departure": departure,
            "destinations": destinations,
            "start_date": start_date,
            "duration_days": duration_days,
            "budget_total": budget_total,
            "researcher_count": researcher_count,
            "sections": {},
        }

        # Section 1: 大交通（去程 + 返程）
        per_dest_budget = budget_total / (len(destinations) + 1) if destinations else budget_total
        for dest in destinations:
            key = f"transport_to_{dest}"
            transport = self.plan_field_site_access(departure, dest, start_date, per_dest_budget)
            plan["sections"][key] = transport

        # Section 2: 每地住宿
        for i, dest in enumerate(destinations):
            key = f"accommodation_{i+1}_{dest}"
            check_in = start_date
            days_at_this = max(1, duration_days // len(destinations))
            check_out_dt = f"2026-12-{min(31, 1 + days_at_this)}"  # 简化处理
            accommodation = self.find_accommodation_for_field_work(
                dest, check_in, check_out_dt, per_dest_budget / days_at_this, duration_days
            )
            plan["sections"][key] = accommodation

        # Section 3: 每地文化/景点分析
        for i, dest in enumerate(destinations):
            key = f"culture_{i+1}_{dest}"
            culture = self.analyze_field_site_culture_and_attractions(dest)
            plan["sections"][key] = culture

        # Section 4: AI语义综合搜索
        ai_query = f"从{departure}出发，{duration_days}天调研{', '.join(destinations)}，人均预算{int(per_dest_budget)}元，推荐行程和住宿"
        ai_result = self.ai_search(ai_query)
        if ai_result.get("status") == 0:
            plan["sections"]["ai_recommendation"] = ai_result.get("data", {})

        # Section 5: 补充需求（签证/保险/租车等）
        supplement_queries = [
            f"{destinations[0] if destinations else '目标地'} 旅游保险",
            f"{destinations[0] if destinations else '目标地'} 租车服务",
        ]
        supplements = {}
        for query in supplement_queries:
            kw = self.keyword_search(query)
            if kw.get("status") == 0:
                supplements[query] = kw.get("data", {}).get("itemList", [])[:5]
        plan["sections"]["supplements"] = supplements

        return plan
