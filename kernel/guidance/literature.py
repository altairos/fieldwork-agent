"""三宿主共享的文献检索策略；不受 Tier S 阶段提示词替换影响。"""

LITERATURE_GUIDANCE = """文献检索工作流（search_literature）：
1. 先使用用户输入与项目快照中的 topic、research_questions、discipline、target_location。
   将研究问题拆为核心现象、研究对象/人群、情境/地区、理论、方法五个可选维度。
   缺信息先形成暂定解释、注明假设；只有连核心对象/现象都无法判断时才先问一个关键问题。
   不把访谈人数、旅行时间、预算或所有维度一起塞进检索词；调研日期不等于文献出版年。
2. 自主设计2-4路 search_matrix，每路 label、purpose、groups；每组1-4个英文同义词/学术短语，
   组内OR、组间AND。各路保留核心现象，分别承担核心经验(core)、理论(theory)、方法(method)
   或区域比较(context)。按实际研究需要选路线，不强行凑齐；理论路线可跨地域、方法路线可放宽
   人群限制。中文词保留用于中文平台入口；学科名不直接追加到所有查询。
   示例：宗族变迁 → core: [["lineage", "kinship"], ["social change", "transformation"], ["rural China"]]；
   theory: [["kinship", "lineage"], ["social reproduction", "social organization"]]。
   这是格式示例，必须按本用户问题重做，不照抄。groups里只放字面词项，不手写引号/布尔符/通配符。
3. 首次 stage=probe，每路最多4篇，max_results是所有路线合计（默认8，硬上限20）。
   默认 search_field=title_and_abstract；核验已知题名用title，不需要矩阵。不得自动翻页。
   用户没要求时，不设近五年或仅OA硬限制；经典理论不限年份，最新进展可单独限年。
4. 先检查 query_reports 与候选标题摘要：broad（命中>500）只说明范围过宽，应增加对象/机制
   锚点或收窄字段；empty/narrow 换同义词或去掉一个非核心限制，仍保留研究问题锚点。
   命中数阈值是调整线索，不是质量评分；若试检已覆盖研究问题就停止。必要时只改不足路线，
   stage=focused复检（每路最多8篇）；不要重跑已充分覆盖的路线，不穷举同义词组合。
   Web/CLI每轮最多3次文献工具调用、8路API请求、24篇返回名额；达到任一预算即基于已有结果回答。
5. 依据标题、摘要与研究问题匹配，区分直接相关、理论借鉴、方法参考、背景比较。
   优先直接相关并兼顾经典与新研究，不能只按被引数推荐；每篇说明具体支持哪个问题/章节。
   无摘要只能标“题名初筛，待核验”；不推断未提供的方法、研究发现或全文结论。
   各路线/各轮重复DOI或OpenAlex ID只推荐一次。通常最终推荐5-8篇；确实不足时如实给少量，
   不为凑数降低相关性，不声称检索完整或把此流程叫系统综述。
6. 回复简述实际采用的检索维度/范围与调整理由，附可追溯作者、年份、标题、DOI或OpenAlex链接。
   total/returned_count是本次实际返回数，retrieved_count是采样条数，unique_count是去重候选数；
   matched_total只是数据库潜在命中数，绝不能称为“已找到/已阅读/已筛选上万篇”。
   多路matched_total有重叠，禁止求和为总文献数；summarized_count少于returned_count时，只引用摘要中
   实际提供的条目。中文平台链接只是补检入口，不是已核验的中文文献或全文命中。
"""
