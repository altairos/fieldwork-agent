"""三宿主共享的旅行决策约定；只说明已经实现的工具入参。"""

TRAVEL_GUIDANCE = """旅行与田野可行性：
交通决策不能只比较最低价。compose_itinerary可通过constraints指定用户明确的最长交通时长、
最大步行距离、大交通最晚到达枢纽时间、换乘缓冲与cost/time偏好；缺失日期、时区或运营时间
必须标为待核实。站点运营窗口内不等于实际可乘，不能承诺座位或现场进入条件。
用户明确安排各点研究活动时，用night_allocation按目的地顺序分配夜数，合计等于duration_days-1；
未知时默认分配只是规划假设，不写成用户已确认的研究安排。
需要比较住宿与通勤且已有夜数/访问天数时，profile_field_site.comparison可传nights、visit_days、
rooms、researcher_count、可选hub_location、max_commute_minutes和daily_available_minutes。
预算房费与往返通勤费分别说明，source未说明报价单位时仅作情景估算；没有每日可用时间基线，
不编造有效研究小时。无地址核对不把飞猪酒店与同名高德POI认定为同一个地点。
"""
