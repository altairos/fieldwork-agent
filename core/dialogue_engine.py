"""
引导式对话引擎 — 四阶段访谈流程
深度集成 FlyAI 8 个工具的意图识别和工具调用决策
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from .state import ProjectState


class DialogueEngine:
    """引导式田野调查访谈引擎"""

    SYSTEM_PROMPTS = {
        ProjectState.PHASE_RESEARCH_POSITIONING: """\
你是田野罗盘 Agent，专门为人类学/社会学田野调查者设计。当前处于"研究定位"阶段。

你需要通过温和但有结构的方式，帮助研究者厘清以下信息：

1. **学科取向**：人类学/社会学/政治学/跨学科？理论框架倾向？
2. **研究问题**：初步的研究问题或关注焦点是什么？越具体越好。
3. **田野点确定程度**：已明确地点 / 有区域范围 / 完全开放由你推荐？
4. **调研规模**：单人还是团队？预计调研时长（短期2-4周、中期1-3月、长期3月以上）？
5. **经费来源与额度**：是否有基金支持？大致预算范围？
6. **时间窗口**：希望在什么时间段进行调研？是否有硬性截止日期？

请一次只问2-3个问题，不要一次性抛出所有问题。根据回答灵活调整追问方向。如果研究者提到某个模糊概念（如"少数民族社区"），主动帮ta细化到可能的具体地点范围。语气专业但不刻板，像一位有经验的导师在指导研究生。""",

        ProjectState.PHASE_FIELD_SITE_PROFILES: """\
你是田野罗盘 Agent。当前处于"田野点画像"阶段。

你已经了解了研究者的基本需求，现在需要利用飞猪的旅行搜索能力，为候选田野点构建详细画像。对于每个候选地点，请从以下维度收集和分析信息：

1. **交通可达性**：使用航班和火车搜索了解大交通成本和时间
2. **住宿条件**：搜索当地民宿和酒店，评估价格范围和适合驻村的条件
3. **文化资源**：搜索当地的宗教场所、古镇古村、博物馆等文化景点
4. **生活配套**：搜索当地美食、交通出行方式等日常生活信息
5. **已有研究基础**：该地是否有其他学者做过研究？可形成学术对话吗？

请使用飞猪搜索工具获取真实数据来辅助判断。将搜索结果与研究者的自有资料交叉验证。当研究者确认了最终田野点后，再进入下一阶段。""",

        ProjectState.PHASE_ITINERARY_PLANNING: """\
你是田野罗盘 Agent。当前处于"行程规划"阶段。

基于已确定的田野点和研究设计，现在需要制定详细的旅行方案。请充分利用飞猪的全部搜索能力：

1. **大交通安排**：对比航班和火车的价格、时间、舒适度，选择最优方案
2. **小交通衔接**：从交通枢纽到田野点的最后一公里方案
3. **住宿匹配**：按调研阶段分配不同住宿（初期住镇上，后期驻村）
4. **调研节奏**：适应期→深入期→补充期的时间分配
5. **缓冲设计**：预留应对天气、交通延误的机动时间
6. **备选路线**：如果首选不可达的替代方案

使用飞猪搜索获取具体的航班、火车、酒店、POI信息，整合成可执行的日程表。输出要足够详细，达到可以直接提交给基金委审批的程度。嵌入图片链接和预订链接。""",

        ProjectState.PHASE_PROPOSAL_GENERATION: """\
你是田野罗盘 Agent。当前处于"方案生成"阶段。

请将前面收集的所有信息整合成一份完整的 Research Proposal（研究方案），格式要求：

# [研究标题]

## 一、研究背景与问题提出
## 二、田野点选择依据（含交通/住宿可行性分析）
## 三、研究设计与方法论
## 四、详细行程安排（含具体航班/车次/酒店信息）
## 五、预算明细（基于实时搜索数据）
## 六、风险评估与伦理预案
## 七、预期成果
## 八、参考文献

确保语言学术化、正式，符合基金委评审标准。数据具体而非泛泛而谈。适当嵌入图片链接和预订链接。""",

        ProjectState.PHASE_FINISHED: """\
田野旅行方案已完成。你可以：

1. 对方案进行逐段修改
2. 调整某个具体细节
3. 导出为新的版本
4. 开始一个新的调研项目

保持专业的学术写作风格，随时准备根据反馈迭代方案。""",
    }

    # 各阶段的引导问题模板
    GUIDING_QUESTIONS = {
        ProjectState.PHASE_RESEARCH_POSITIONING: [
            "请问您的学科背景和研究取向是什么？（人类学/社会学/政治学/跨学科？）",
            "您初步的研究问题或关注焦点是什么？",
            "您对田野地点的确定程度如何？（已明确地点 / 有区域范围 / 希望我推荐）",
            "这次调研是单人还是团队？预计多长时间？",
            "经费来源和大致预算范围是怎样的？",
            "希望在什么时间段进行调研？",
        ],
        ProjectState.PHASE_FIELD_SITE_PROFILES: [
            "请先告诉我您关注的目标区域或具体地点名称，我来帮您收集详细信息。",
            "除了主田野点，是否需要考察周边地区作为备选？",
            "您对住宿有什么特殊要求？（靠近调研点优先 / 经济型优先 / 网络条件要好）",
        ],
        ProjectState.PHASE_ITINERARY_PLANNING: [
            "您的出发城市是哪里？",
            "您需要往返票还是单程票？",
            "团队人数是多少？（影响机票总价估算）",
            "调研期间是否需要中途换点？",
        ],
    }

    def __init__(self, project_state: ProjectState):
        self.project = project_state

    @property
    def system_prompt(self) -> str:
        return self.SYSTEM_PROMPTS.get(
            self.project.phase,
            self.SYSTEM_PROMPTS[ProjectState.PHASE_RESEARCH_POSITIONING]
        )

    # ══════════════════════════════════════════
    # 工具调用决策 — 深度意图识别
    # ══════════════════════════════════════════

    def decide_tool_calls(self, user_message: str) -> List[Dict[str, Any]]:
        """根据用户消息的意图决定需要调用的 FlyAI 工具"""
        info = self.project.get_all_researcher_info() if self.project else {}
        target_location = info.get("target_location", "")
        departure_city = info.get("departure_city", "")
        travel_date = info.get("travel_date", "")
        phase = self.project.phase

        # ─── 优先提取地点信息（如果尚未提取）─────────
        if not target_location or not departure_city:
            extracted_locations = self._extract_locations_from_message(
                user_message, info
            )
            for k, v in extracted_locations.items():
                if v:
                    info[k] = v

        target_location = info.get("target_location", target_location)
        departure_city = info.get("departure_city", departure_city)

        lower_msg = user_message.lower()
        calls = []

        # ═══════════════════════════════════════
        # 区域探索意图（任何阶段都适用）— 提到省份但未确定具体地点
        # ═══════════════════════════════════════
        province_keywords = ["云南", "贵州", "四川", "广西", "西藏", "新疆",
                            "甘肃", "青海", "湖南", "湖北", "福建", "广东", "浙江",
                            "江苏", "安徽", "江西", "河南", "河北", "山东", "山西",
                            "陕西", "宁夏", "内蒙古"]
        has_province = any(p in user_message for p in province_keywords)
        if has_province and not target_location:
            calls.append({
                "tool": "keyword_search",
                "args": {"query": f"{user_message} 村落 民族 调研"},
                "purpose": "探索目标区域基本情况",
            })

        # ═══════════════════════════════════════
        # 阶段 B: 田野点画像 — 全面采集
        # ═══════════════════════════════════════
        if phase == ProjectState.PHASE_FIELD_SITE_PROFILES and target_location:
            calls.extend([
                {
                    "tool": "search_hotel",
                    "args": {"dest_name": target_location, "hotel_types": "民宿,客栈",
                             "sort": "price_asc"},
                    "purpose": f"查找{target_location}适合驻村的民宿",
                },
                {
                    "tool": "search_hotel",
                    "args": {"dest_name": target_location, "sort": "distance_asc"},
                    "purpose": f"查找{target_location}酒店备选",
                },
                {
                    "tool": "search_poi",
                    "args": {"city_name": target_location, "category": "人文古迹"},
                    "purpose": f"检索{target_location}人文古迹",
                },
                {
                    "tool": "search_poi",
                    "args": {"city_name": target_location, "category": "宗教场所"},
                    "purpose": f"检索{target_location}宗教文化场所",
                },
                {
                    "tool": "search_poi",
                    "args": {"city_name": target_location, "category": "古镇古村"},
                    "purpose": f"检索{target_location}古镇古村",
                },
                {
                    "tool": "search_poi",
                    "args": {"city_name": target_location, "category": "博物馆"},
                    "purpose": f"检索{target_location}博物馆资源",
                },
                {
                    "tool": "ai_search",
                    "args": {"query": f"{target_location} 旅游 文化 经济 适合长期驻村调研"},
                    "purpose": f"AI语义理解{target_location}综合情况",
                },
                {
                    "tool": "keyword_search",
                    "args": {"query": f"{target_location} 文化活动 节庆 民俗"},
                    "purpose": f"搜索{target_location}当地文化活动和节庆",
                },
            ])

        # ═══════════════════════════════════════
        # 阶段 C: 行程规划 — 交通+住宿+文化全覆盖
        # ═══════════════════════════════════════
        elif phase == ProjectState.PHASE_ITINERARY_PLANNING:
            if departure_city and target_location:
                calls.extend([
                    {
                        "tool": "search_flight",
                        "args": {
                            "origin": departure_city,
                            "destination": target_location,
                            "dep_date": travel_date or "",
                            "sort_type": "3",
                        },
                        "purpose": f"航班方案({departure_city}→{target_location})",
                    },
                    {
                        "tool": "search_train",
                        "args": {
                            "origin": departure_city,
                            "destination": target_location,
                            "dep_date": travel_date or "",
                            "sort_type": "4",
                        },
                        "purpose": f"高铁备选方案({departure_city}→{target_location})",
                    },
                    {
                        "tool": "search_hotel",
                        "args": {"dest_name": target_location, "hotel_types": "民宿,客栈",
                                 "sort": "price_asc"},
                        "purpose": f"田野点附近住宿(民宿优先)",
                    },
                    {
                        "tool": "search_hotel",
                        "args": {"dest_name": target_location, "sort": "distance_asc"},
                        "purpose": f"按距离筛选住宿",
                    },
                    {
                        "tool": "search_poi",
                        "args": {"city_name": target_location, "category": "人文古迹"},
                        "purpose": f"了解{target_location}人文景点",
                    },
                    {
                        "tool": "search_poi",
                        "args": {"city_name": target_location, "category": "宗教场所"},
                        "purpose": f"了解{target_location}宗教文化",
                    },
                    {
                        "tool": "keyword_search",
                        "args": {"query": f"{target_location} 租车 交通出行"},
                        "purpose": f"当地租车和交通服务",
                    },
                    {
                        "tool": "ai_search",
                        "args": {"query": f"从{departure_city}到{target_location}调研行程安排 预算有限"},
                        "purpose": "AI综合行程推荐",
                    },
                ])

        # ═══════════════════════════════════════
        # 基于关键词的动态意图识别（各阶段通用）
        # ═══════════════════════════════════════
        else:
            # 航班意图
            flight_keywords = ["航班", "机票", "飞机", "flight", "航空", "飞往"]
            has_flight = any(kw in user_message for kw in flight_keywords)
            if has_flight:
                if departure_city and target_location:
                    calls.append({
                        "tool": "search_flight",
                        "args": {
                            "origin": departure_city,
                            "destination": target_location,
                            "dep_date": travel_date or "",
                            "sort_type": "3",
                        },
                        "purpose": "搜索航班方案",
                    })
                else:
                    calls.append({
                        "tool": "keyword_search",
                        "args": {"query": user_message},
                        "purpose": "广义搜索航班信息",
                    })

            # 火车意图
            train_keywords = ["火车", "高铁", "列车", "railway", "动车"]
            has_train = any(kw in user_message for kw in train_keywords)
            if has_train:
                if departure_city and target_location:
                    calls.append({
                        "tool": "search_train",
                        "args": {
                            "origin": departure_city,
                            "destination": target_location,
                            "dep_date": travel_date or "",
                            "sort_type": "4",
                        },
                        "purpose": "搜索火车方案",
                    })
                else:
                    calls.append({
                        "tool": "keyword_search",
                        "args": {"query": user_message},
                        "purpose": "广义搜索火车信息",
                    })

            # 住宿意图
            hotel_keywords = ["酒店", "住宿", "民宿", "客栈", "hotel", "accommodation", "住"]
            has_hotel = any(kw in user_message for kw in hotel_keywords)
            if has_hotel:
                if target_location:
                    calls.append({
                        "tool": "search_hotel",
                        "args": {"dest_name": target_location, "sort": "price_asc"},
                        "purpose": "搜索住宿选项",
                    })
                else:
                    calls.append({
                        "tool": "keyword_search",
                        "args": {"query": user_message},
                        "purpose": "广义搜索住宿信息",
                    })

            # POI/文化/景点意图
            poi_keywords = ["景点", "POI", "文化", "民俗", "宗教", "古镇", "博物馆",
                           "attraction", "site", "heritage", "temple"]
            has_poi = any(kw in user_message for kw in poi_keywords)
            if has_poi:
                if target_location:
                    calls.append({
                        "tool": "search_poi",
                        "args": {"city_name": target_location},
                        "purpose": "搜索当地景点和文化场所",
                    })
                else:
                    calls.append({
                        "tool": "keyword_search",
                        "args": {"query": user_message},
                        "purpose": "广义搜索景点信息",
                    })

            # 补充需求意图
            supplement_keywords = ["签证", "保险", "租车", "visa", "insurance",
                                   "car rental", "wifi", "sim卡"]
            has_supplement = any(kw in user_message for kw in supplement_keywords)
            if has_supplement:
                calls.append({
                    "tool": "keyword_search",
                    "args": {"query": user_message},
                    "purpose": "搜索补充服务信息",
                })

            # AI语义搜索意图（开放式请求）
            ai_keywords = ["推荐", "建议", "recommend", "suggest", "plan", "best", "最优"]
            has_ai = any(kw in user_message for kw in ai_keywords)
            if has_ai:
                calls.append({
                    "tool": "ai_search",
                    "args": {"query": user_message},
                    "purpose": "AI语义理解并推荐",
                })

            # 区域探索意图（提到了省份但还没确定具体地点）
            province_keywords = ["云南", "贵州", "四川", "广西", "西藏", "新疆",
                                "甘肃", "青海", "湖南", "湖北"]
            has_province = any(p in user_message for p in province_keywords)
            if has_province and not target_location:
                calls.append({
                    "tool": "keyword_search",
                    "args": {"query": f"{user_message} 村落 民族 调研"},
                    "purpose": "探索目标区域基本情况",
                })

            # 万豪相关意图
            marriott_keywords = ["万豪", "marriott", "高端", "luxury"]
            has_marriott = any(kw in user_message for kw in marriott_keywords)
            if has_marriott and target_location:
                calls.append({
                    "tool": "search_marriott_hotel",
                    "args": {"dest_name": target_location, "sort": "price_asc"},
                    "purpose": "搜索万豪旗下高端酒店",
                })

            # 套餐/体验意图
            package_keywords = ["套餐", "下午茶", "experience", "体验", "package"]
            has_package = any(kw in user_message for kw in package_keywords)
            if has_package:
                calls.append({
                    "tool": "search_marriott_package",
                    "args": {"keyword": target_location or "云南"},
                    "purpose": "搜索酒店套餐和体验产品",
                })

        return calls

    def _extract_locations_from_message(
        self, message: str, existing_info: dict
    ) -> Dict[str, str]:
        """从消息中提取出发城市和目的地"""
        import re
        locations = {}
        lower = message.lower()

        # 中文城市/地名列表（用于匹配）
        known_cities = [
            "北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "重庆",
            "武汉", "西安", "长沙", "昆明", "贵阳", "拉萨", "乌鲁木齐",
            "银川", "西宁", "兰州", "福州", "厦门", "南宁", "桂林",
            "大理", "丽江", "香格里拉", "腾冲", "芒市", "景洪",
            "西双版纳", "腾冲", "文山", "红河", "楚雄", "玉溪",
            "遵义", "毕节", "六盘水", "安顺", "黔东南", "黔南", "黔西南",
            "澳门", "香港", "台北", "高雄", "台中",
        ]

        # 省份关键词
        provinces = ["云南", "贵州", "四川", "广西", "西藏", "新疆",
                     "甘肃", "青海", "湖南", "湖北", "福建", "广东", "浙江",
                     "江苏", "安徽", "江西", "河南", "河北", "山东", "山西",
                     "陕西", "宁夏", "内蒙古"]

        # ─── 提取目的地 ──────────────────────
        if not existing_info.get("target_location"):
            dest_found = None

            # 模式1: "到/去/前往 + 地名"
            m = re.search(r"(?:到|去|前往|visiting)\s*([^\s,，。]+)", message)
            if m:
                candidate = m.group(1)
                # 检查是否是已知城市或包含已知城市
                for city in known_cities:
                    if city in candidate or candidate == city:
                        dest_found = city
                        break

            # 模式2: "地名 + 的 + 村落/社区/乡镇"
            if not dest_found:
                m = re.search(r"([^\s,，。]+?)\s*(?:的)?(?:村落|社区|乡镇|县|镇|乡)", message)
                if m:
                    candidate = m.group(1)
                    for city in known_cities:
                        if city in candidate:
                            dest_found = city
                            break

            # 模式3: 直接匹配已知城市列表
            if not dest_found:
                for city in known_cities:
                    if city in message:
                        dest_found = city
                        break

            if dest_found:
                locations["target_location"] = dest_found

        # ─── 提取出发城市 ──────────────────────
        if not existing_info.get("departure_city"):
            dep_found = None

            # 模式1: "从 + 地名 + 到/前往"
            m = re.search(r"从\s*([^\s,，。]+?)\s*(?:到|前往|出发)", message)
            if m:
                candidate = m.group(1)
                for city in known_cities + provinces:
                    if city in candidate:
                        dep_found = city
                        break

            # 模式2: "出发城市" 关键词
            if not dep_found:
                m = re.search(r"(?:出发|from).+?([^\s,，。]+)", message)
                if m:
                    candidate = m.group(1)
                    for city in known_cities + provinces:
                        if city in candidate:
                            dep_found = city
                            break

            if dep_found:
                locations["departure_city"] = dep_found

        return locations

    def _infer_location_from_context(self, message: str, info: dict) -> str:
        """当没有预存地点时，从消息中推断一个合理的地点用于工具调用"""
        import re
        known_cities = [
            "北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "重庆",
            "武汉", "西安", "长沙", "昆明", "贵阳", "大理", "丽江",
            "拉萨", "乌鲁木齐", "福州", "厦门", "南宁", "桂林",
            "西双版纳", "腾冲", "芒市", "景洪",
        ]
        for city in known_cities:
            if city in message:
                return city
        return ""

    # ─── 信息提取 ──────────────────────────────

    def extract_researcher_info_from_message(self, message: str) -> Dict[str, str]:
        """从自然语言回复中提取结构化信息"""
        info = {}
        lower = message.lower()

        # 学科
        if "人类学" in message or "anthropology" in lower:
            info["research_discipline"] = "人类学"
        elif "社会学" in message or "sociology" in lower:
            info["research_discipline"] = "社会学"
        elif "政治学" in message or "politics" in lower:
            info["research_discipline"] = "政治学"
        elif "跨学科" in message or "interdisciplinary" in lower:
            info["research_discipline"] = "跨学科"

        # 调研规模
        if "团队" in message or "多人" in message:
            info["scale"] = "团队调研"
        elif "单人" in message or "独自" in message:
            info["scale"] = "单人调研"

        # 预算关键词
        if "基金" in message or "grant" in lower:
            info["budget_source"] = "基金支持"
        elif "自费" in message or "self-funded" in lower:
            info["budget_source"] = "自费"

        # 地点关键词提取
        places = ["云南", "贵州", "四川", "广西", "西藏", "新疆", "甘肃", "青海", "湖南", "湖北", "福建", "广东", "浙江"]
        for place in places:
            if place in message:
                info["target_region"] = place
                break

        return info

    # ─── 处理用户消息 ──────────────────────────────

    def process_user_message(self, user_message: str) -> Dict[str, Any]:
        msg_meta = {"phase": self.project.phase}

        # 记录用户消息
        self.project.append_chat("user", user_message, msg_meta)

        # 尝试提取结构化信息
        extracted = self.extract_researcher_info_from_message(user_message)
        for key, value in extracted.items():
            self.project.set_researcher_info(key, value)

        # 决定需要调用的工具
        tool_calls = self.decide_tool_calls(user_message)

        # 检查是否应该推进阶段
        should_advance = False
        if self.project.phase == ProjectState.PHASE_RESEARCH_POSITIONING:
            info = self.project.get_all_researcher_info()
            required_keys = ["research_discipline", "research_topic", "field_site_status", "scale", "budget", "time_window"]
            answered = sum(1 for k in required_keys if info.get(k))
            should_advance = answered >= 4
        elif self.project.phase == ProjectState.PHASE_FIELD_SITE_PROFILES:
            field_sites = self.project.state.get("field_sites", [])
            confirmed = any(s.get("confirmed") for s in field_sites)
            should_advance = confirmed

        if should_advance:
            old_phase = self.project.phase
            self.project.advance_phase()
            msg_meta["phase_transition"] = {"from": old_phase, "to": self.project.phase}

        return {
            "system_prompt": self.system_prompt,
            "user_message": user_message,
            "guiding_questions": self.GUIDING_QUESTIONS.get(self.project.phase, []),
            "phase": self.project.phase,
            "messages_to_llm": self.project.get_recent_messages(20),
            "tool_calls": tool_calls,
            "msg_meta": msg_meta,
        }
