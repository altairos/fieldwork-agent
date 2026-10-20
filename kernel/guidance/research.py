"""按任务检查基础信息并构建动态研究状态；不以字段齐备宣称研究质量。"""
from fieldwork_agent.kernel.research_state import question_fingerprint, scope_fingerprint

POSITIONING_FIELDS = ("purpose", "discipline", "topic", "scale", "budget", "time_window", "research_questions")
TASK_REQUIREMENTS = {
    "literature": ("topic",),
    "field_site_comparison": ("topic", "research_questions"),
    "method_design": ("topic", "research_questions"),
    "itinerary": ("target_location", "departure_city", "travel_date"),
    "proposal_draft": ("topic", "research_questions"),
}

RESEARCH_WORKFLOW_GUIDANCE = """研究任务与持续状态：
学术取向research_orientation、目标院校target_institution、院系target_department、学位target_degree、
导师target_advisor只保存用户明确表达的信息，与田野地点target_location分别处理；不从学派地名、
院校所在地推定田野地点，不从目标院系推定用户学科。简称有歧义时保留原表达，必要时澄清。
用户更正时写完整新值，撤回学术目标时用clear_fields列出撤回字段；推荐关系不代表用户选择。
学术关系、学派起源及当前招生信息必须有对应来源，当前尚未接入RAG证据时不能声称已经核验。
根据当前请求选择任务，不把阶段当成不可回头的问卷。初步文献探索有主题或问题即可；
田野比较/方法设计需要一个已明确的核心问题；真实行程查询还需目的地、出发地和具体出行日期。
预算、人数、用途等在实际需要时补充，不为文献探索或问题建构索取全部定位信息。
readiness只检查基础字段，不证明学术质量；完整方案仍需工具规定的真实材料与渠道。
文献探索可在任何阶段进行：读标题摘要→比较直接相关/理论借鉴/方法参考→指出解释差异或材料
缺口→提出可修改的候选问题→用户采纳后更新research_questions及检索矩阵。不要仅凭命中数断言研究空白。
用update_research_context保存以下状态：
1. 原有topic/research_questions等字段只写用户明确表达或采纳的信息；用户改主题而没有重认问题时，
   旧问题会被标为需重新核验。用户明确沿用旧问题时，把同一问题显式写入以确认新的适用范围。
   用户明确撤回已确认问题且尚无替代时，research_questions写空串，同时提交与旧问题同文的
   rejected问题笔记并记录用户basis；不能只否定笔记却保留旧问题为当前方向。
2. research_notes是按id更新的工作笔记：新增必须有kind、content、origin(user/agent/literature)、
   status(proposed/adopted/rejected/unresolved/superseded)，采纳/否定必须有basis记录用户依据；
   kind只用question/interpretation/method/literature_gap/decision/observation，不自造枚举值；
   文献来源还必须有references（真实DOI/OpenAlex链接/已取回快照）。更新笔记状态使用已有id；
   改写内容时新增条目并将旧条目标为superseded，不能抹掉旧方向。
   proposed是候选，不是用户事实；用户否定的方向不得重复建议。文献笔记只写实际看见的摘要证据。
3. interview_state保存current_focus、open_questions、deferred_fields。用户暂不提供的字段写入
   deferred_fields，不把“暂不知道”填成真实预算/时间。下轮只在该约束真正阻塞当前任务时再问。
   last_user_message/last_assistant_message由CLI/Web轮末记录，不需模型重抄。
project brief会标注文献及笔记属于当前还是旧研究范围；旧范围或未绑定范围的结果先核验，
不能冒充当前问题已经完成的综述。研究主题/问题变化后，重新检查文献矩阵、材料路径、访谈对象
与已有方案；对话记忆和候选笔记不会自动成为正式方案的研究事实。
"""


def task_readiness(ctx: dict, task: str) -> dict:
    if task not in TASK_REQUIREMENTS:
        raise ValueError(f"未知研究任务：{task}")
    def value(key):
        return str(ctx.get(key) or (ctx.get("extras") or {}).get(key) or "").strip()
    missing = [key for key in TASK_REQUIREMENTS[task] if not value(key)]
    if task == "literature" and value("research_questions"):
        missing = []
    if "research_questions" in TASK_REQUIREMENTS[task] and value("research_questions"):
        if value("research_questions") == value("topic"):
            missing.append("research_questions_refinement")
        if ctx.get("research_question_scope") and ctx["research_question_scope"] != question_fingerprint(ctx):
            missing.append("research_questions_reconfirmation")
    return {"ready": not missing, "missing": missing}


def build_research_guidance(ctx: dict) -> str:
    lines = ["任务准备情况（字段检查，不是研究质量评分）："]
    for task in TASK_REQUIREMENTS:
        check = task_readiness(ctx, task)
        lines.append(f"{task}：" + ("基础信息可用" if check["ready"] else "待补/核验 " + "、".join(check["missing"])))
    memory = ctx.get("interview_state") or {}
    if memory.get("current_focus"):
        lines.append("本轮焦点：" + memory["current_focus"])
    deferred = [key for key in memory.get("deferred_fields") or []
                if not ctx.get(key) and not (ctx.get("extras") or {}).get(key)]
    if deferred:
        lines.append("用户暂缓提供（不重复追问，实际阻塞时再补）：" + "、".join(deferred))
    if memory.get("open_questions"):
        lines.append("未解决疑问：" + "；".join(memory["open_questions"][:4]))
    for label, key in (("最近用户说明", "last_user_message"), ("最近助手回应", "last_assistant_message")):
        if memory.get(key):
            lines.append(f"{label}（节选，仅供理解已交流内容）：{memory[key][:400]}")
    notes = ctx.get("research_notes") or []
    # 否定方向优先保留，避免新建议淹没用户已作出的决定；同样有界。
    selected = [n for n in notes if n.get("status") == "rejected"][-4:]
    selected += [n for n in notes if n not in selected][-6:]
    for note in selected:
        scope = "当前范围" if note.get("scope_fingerprint") == scope_fingerprint(ctx) else "旧范围/未绑定，需核验"
        lines.append(f"笔记 {note.get('id')} [{note.get('origin')}/{note.get('status')}；{scope}] "
                     f"{str(note.get('content', ''))[:300]}；依据：{str(note.get('basis', ''))[:160]}"
                     f"；来源：{', '.join(note.get('references') or [])[:300]}")
    return "\n".join(lines)
