"""工具用途说明与明确跨领域误用的出网前检查；不替模型做通用意图分类。"""
import re

from fieldwork_agent.kernel.guidance.travel import TRAVEL_GUIDANCE
from fieldwork_agent.kernel.models import Envelope, ToolError

TOOL_USAGE_GUIDANCE = """工具分工与证据边界（优先于阶段资产中笼统的“用搜索工具”）：
- 学术论文、文献综述、理论、既有研究、民族志著作：用search_literature（OpenAlex）。
  结果是论文元数据/摘要，不代表已读全文；中文平台链接只是补检入口。
- search_keyword、search_ai：飞猪旅行商品搜索，仅用于住宿、租车、签证、保险、餐饮、门票、
  游玩套餐等旅行供给。keyword不是网页搜索，AI不是通用研究助理；二者均不能检索学术材料、
  地方志、人口统计、政策文件或证明当地社会事实。OpenAlex无结果时不能拿飞猪替代查论文。
- search_flights/search_trains/search_hotels/search_marriott_hotels/search_marriott_packages：
  班次、票价与住宿/套餐供给；价格不能当作驻村生活成本或学术证据。
- search_pois：旅游兴趣点、文化场所线索；返回景点商品不能证明其研究价值或当地社会关系。
- amap_geocode/amap_regeocode/amap_text_search/amap_around_search/amap_direction_transit/amap_weather：
  境内定位、行政区划、设施、接驳与天气；不查论文、不替代研究进入条件核验。
- profile_field_site/plan_last_mile/compose_itinerary/analyze_fieldwork_window：组合已有旅行/地理
  能力，仍受源覆盖限制；田野点画像里的“既有研究”应另用search_literature，不用旅行关键词补位。
- estimate_living_cost：带假设与区间的费用估算；compose_proposal/compose_interview_guide/
  suggest_informants：基于现有研究信息生成草稿或候选，不是新的事实来源。
- update_research_context/get_project_brief/save_informants：保存与读取本项目研究状态（含访谈对象登记），不检索外部资料。
需要本工具集没有覆盖的材料时，如实说明并请用户提供来源或使用返回的平台入口，不泛用旅行搜索。
当前没有政府原始资料或通用网页搜索工具；若请求人口统计原表、政策原文或地方志全文，先说明
该来源缺口。仅因出现地名就调用amap_geocode并不能取得这些资料，也不能说定位后就能找到原文；
除非用户另外需要空间定位，否则无需为这个请求调用地理、旅行或论文工具。
调用前核对“需要什么信息→哪种来源能提供→对应工具”；混合请求分别调用，不用一个搜索兜底。
面向用户直接给可执行的回应，不输出<thinking>等内部草稿标签或展开内部工具选择推演。
tool_domain_mismatch说明所选工具不适用，应改用提示中的工具，不能换个模糊关键词继续撞同一源。
"""

TOOL_USAGE_GUIDANCE += "\n" + TRAVEL_GUIDANCE

_ACADEMIC = re.compile(
    r"论文|文献|民族志|学术(?:材料|研究|资料)|参考书目|期刊文章|"
    r"\b(?:literature|bibliograph\w*|ethnograph\w*|scholarly|peer[- ]reviewed|"
    r"research papers?|academic (?:research|papers?|articles?|materials?)|journal articles?)\b", re.I)
_EXPLICIT_ACADEMIC = re.compile(
    r"文献综述|参考文献|学术(?:论文|文献|材料|研究|资料)|"
    r"(?:检索|查找|搜索|收集|找).{0,24}(?:论文|文献)|"
    r"\b(?:scholarly|academic|research|journal)\s+(?:papers?|articles?|literature)\b", re.I)
_TRAVEL_PURCHASE = re.compile(
    r"门票|票价|预订|租车|旅游产品|研学游|酒店价格|住宿价格|"
    r"\b(?:tickets?|booking|hotel prices?|tour packages?|car rental)\b", re.I)


def check_tool_domain(name: str, query: str) -> Envelope | None:
    """只拒绝关键词中明确的学术请求；不猜测“银器传承”等歧义主题的用途。"""
    if name not in {"search_keyword", "search_ai"} or not isinstance(query, str):
        return None
    if not _ACADEMIC.search(query):
        return None
    if _TRAVEL_PURCHASE.search(query) and not _EXPLICIT_ACADEMIC.search(query):
        return None  # 例如“民族志博物馆门票”仍是合法旅行商品需求
    return Envelope(ok=False, error=ToolError(
        code="tool_domain_mismatch",
        message=f"{name}仅检索飞猪旅行商品，不能用于查论文、文献或学术材料；本次未出网。",
        hint="学术材料请改用search_literature并设计检索矩阵；查不到时说明覆盖缺口，不重试飞猪。"))
