"""system prompt + 项目快照 + 历史消息组装（spec §3.3 步骤 1）。"""
from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.guidance.brief import build_project_brief
from fieldwork_agent.kernel.guidance.costs import COST_GUIDANCE
from fieldwork_agent.kernel.repository.protocol import ProjectRepository

_TOOL_GUIDANCE = (
    "工具使用指引：需要真实交通/住宿/景点/文化活动数据时，调用飞猪搜索工具"
    "（search_flights/search_trains/search_hotels/search_pois/search_keyword/search_ai 等）"
    "或聚合能力（profile_field_site/compose_itinerary）。"
    "地理工具（高德）仅覆盖境内：amap_no_result（常见于境外地名）时，明确告知用户"
    "境外田野点的定位/周边/接驳功能正在开发中、后续版本将追加（不要静默跳过），"
    "并改用搜索工具补位。"
    "文档能力（compose_proposal/compose_interview_guide/suggest_informants）用于产出方案。"
    "不要臆造数据；工具不可用、降级或返回数据与田野需求不符时：如实标注质量与缺口，"
    "并把下一步落成对用户的具体引导性提问（地点线索、村庄/县名、本地联系人、"
    "已有材料等），保持对话推进，绝不停在单方陈述。"
    "数据诚实：回复只引用本轮实际调用工具返回的数据；不得声称调用了本轮未调用的工具，"
    "不得把历史轮数据包装成本轮实测——历史数据只能以“此前采样/上一轮显示”表述引用"
    "且不得补造细节（店名/评分/价格等）。回答先直接回应用户当前问题，支撑数据放后。\n\n"
    "职责边界：本助手只服务田野调查研究方案的设计与产出。输出用途（海外申请/"
    "开题/申报等）仅用于校准方案体例与严谨度，不提供留学申请咨询：不问标化成绩、"
    "GPA、写作样本、择校与申请策略；用户提起时温和拉回研究设计。\n\n"
    "交互约定：向用户提问时优先把问题设计成候选答案可枚举的形式。每个可枚举"
    "问题都在回复最末尾单独输出一组带标签的选项块 [[options:问题短标签]]选项甲|选项乙|选项丙"
    "[[/options]]（2-4 个选项、以 | 分隔、每个 ≤ 12 字、标签 ≤ 8 字，与正文中该问题"
    "的备选一致）；本回合问了几个可枚举问题就给几组、一问一组一一对应，前端会把"
    "每组渲染成一行带小标题的快捷按钮供用户逐组点选；确实无法枚举的开放式问题"
    "直接提问、不带标记。"
)

# 每轮动态提醒（追加在 user 消息之后）：长对话中 system prompt 的交互约定
# 指令会随轮次衰减（生产实录：前 2 回合规守 [[options]]、后 2 回合遗忘），
# 尾部短提醒以最近距离对抗衰减；仅影响当轮 LLM 输入，不落库。
_OPTIONS_REMINDER = (
    "提醒：本回合每个可枚举的问题，都要在回复最末尾为它单独输出一组带标签"
    "的选项块 [[options:问题短标签]]选项|选项|选项[[/options]]（2-4 项、以 | 分隔、"
    "每项 ≤ 12 字）；问了几个可枚举问题就给几组，一问一组一一对应。无法枚举的"
    "开放式问题不需要。"
)


def build_snapshot(repo: ProjectRepository, pid: str) -> str:
    """F04 有界项目摘要：kernel brief 推导（搜索缓存收 repo 索引传入）。"""
    state = repo.get_state(pid)
    return build_project_brief(
        state, cached_searches=repo.list_cached_searches(pid))


def build_system_prompt(phase: str, snapshot: str) -> str:
    return "\n\n".join([phases.time_anchor(), phases.phase_prompt(phase),
                        COST_GUIDANCE, _TOOL_GUIDANCE, snapshot])


def build_messages(repo: ProjectRepository, pid: str, user_text: str,
                   phase: str, history_limit: int = 30) -> list[dict]:
    snapshot = build_snapshot(repo, pid)
    system = build_system_prompt(phase, snapshot)
    history = repo.get_messages(pid, limit=history_limit)
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": m["role"], "content": m["content"]}
             for m in history if m.get("role") in ("user", "assistant")]
    msgs.append({"role": "user", "content": user_text})
    msgs.append({"role": "system", "content": _OPTIONS_REMINDER})
    return msgs
