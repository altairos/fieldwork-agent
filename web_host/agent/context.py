"""system prompt + 项目快照 + 历史消息组装（spec §3.3 步骤 1）。"""
from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.repository.protocol import ProjectRepository

_TOOL_GUIDANCE = (
    "工具使用指引：需要真实交通/住宿/景点/文化活动数据时，调用飞猪搜索工具"
    "（search_flights/search_trains/search_hotels/search_pois/search_keyword/search_ai 等）"
    "或聚合能力（profile_field_site/compose_itinerary）。"
    "文档能力（compose_proposal/compose_interview_guide/suggest_informants）用于产出方案。"
    "不要臆造数据；工具不可用时如实说明并给出可执行的下一步。"
)


def build_snapshot(repo: ProjectRepository, pid: str) -> str:
    state = repo.get_state(pid)
    ctx = state.get("research_context") or {}
    filled = [f"{k}={v}" for k, v in ctx.items() if k != "extras" and str(v).strip()]
    extras = ctx.get("extras") or {}
    filled += [f"{k}={v}" for k, v in extras.items()]
    n_sites = len(state.get("field_sites") or [])
    n_inf = len(state.get("informants") or [])
    n_prop = len(state.get("proposals") or [])
    return (f"当前项目快照：已采集[{'；'.join(filled) if filled else '（空）'}]；"
            f"候选田野点 {n_sites} 个、访谈对象 {n_inf} 位、方案草稿 {n_prop} 份。")


def build_system_prompt(phase: str, snapshot: str) -> str:
    return "\n\n".join([phases.phase_prompt(phase), _TOOL_GUIDANCE, snapshot])


def build_messages(repo: ProjectRepository, pid: str, user_text: str,
                   phase: str, history_limit: int = 30) -> list[dict]:
    snapshot = build_snapshot(repo, pid)
    system = build_system_prompt(phase, snapshot)
    history = repo.get_messages(pid, limit=history_limit)
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": m["role"], "content": m["content"]}
             for m in history if m.get("role") in ("user", "assistant")]
    msgs.append({"role": "user", "content": user_text})
    return msgs
