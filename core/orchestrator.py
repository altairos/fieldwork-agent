"""
田野罗盘 Agent — 内核宿主（orchestrator）

Task 9 重写：从"关键词路由 + DialogueEngine/FlyAITools/DashScopeChat 旧宿主"
改为**能力内核宿主**。职责仅为组装与编排，一切业务能力来自 ``kernel``，共享
回合循环来自 ``runtime.loop``：

- 组装 ``JsonRepository`` / ``FlyAIClient`` / ``LLMClient``（可选）/ guidance；
- ``client_kernel_dispatch``：把 LLM 的 function calling 分派到 registry 能力
  （搜索类透传 flyai，聚合/文档类注入 client/llm），任何异常统一折进包络；
- ``chat``：extract 采集 → 落研究上下文 → 组装 system → run_tool_loop →
  轮末事件落盘 → ``check_advance`` **确定性**判定推进（绝不由 LLM 宣布）；
- 无 LLM 时回落旧 ``_rule_based_reply`` 规则文案路径（降级可用）。

依赖方向：core/orchestrator → runtime → kernel；kernel 绝不反向 import 本模块。
"""
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fieldwork_agent.kernel.aggregation.itinerary import compose_itinerary
from fieldwork_agent.kernel.aggregation.site_profile import profile_field_site
from fieldwork_agent.kernel.documents.informants import suggest_informants
from fieldwork_agent.kernel.documents.proposal import (
    compose_interview_guide,
    compose_proposal,
)
from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.guidance.extract import extract_locations, extract_researcher_info
from fieldwork_agent.kernel.llm import LLMClient
from fieldwork_agent.kernel.models import (
    Envelope,
    FieldSiteRecord,
    InformantRecord,
    ResearchContext,
    ToolError,
)
from fieldwork_agent.kernel.registry import build_tool_schemas, capabilities
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.sources.flyai import FlyAIClient
from fieldwork_agent.runtime.loop import run_tool_loop
from pydantic import ValidationError

from .config import Config

# registry 全局表只供 schema，不经由它裸调：宿主用下表把能力名绑到真实实现与注入方式
# inject: "client" → 注入 self.flyai；"llm" → 注入 self.llm
_CAPABILITY_DISPATCH: dict[str, tuple[Callable[..., Any], str]] = {
    "profile_field_site": (profile_field_site, "client"),
    "compose_itinerary": (compose_itinerary, "client"),
    "compose_proposal": (compose_proposal, "llm"),
    "compose_interview_guide": (compose_interview_guide, "llm"),
    "suggest_informants": (suggest_informants, "llm"),
}

# FlyAIClient 的 8 个原子搜索原语（透传，不经 registry args_model 校验）
_SEARCH_METHODS: frozenset[str] = frozenset({
    "search_flights", "search_trains", "search_hotels", "search_marriott_hotels",
    "search_marriott_packages", "search_pois", "search_keyword", "search_ai",
})


def _s(name: str, description: str, props: dict, required: list[str]) -> dict:
    """构造一条 OpenAI function 工具 schema（供搜索原语进 schema）。"""
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": required}}}


def _str_prop(desc: str) -> dict:
    return {"type": "string", "description": desc}


# 8 个搜索工具的 schema（与 FlyAIClient 方法形参逐一对齐；聚合/文档工具走 build_tool_schemas）
_SEARCH_TOOL_SCHEMAS: list[dict] = [
    _s("search_flights", "航班搜索：跨省/跨国调研大交通", {
        "origin": _str_prop("出发城市"), "destination": _str_prop("目的地"),
        "dep_date": _str_prop("出发日期 YYYY-MM-DD"), "back_date": _str_prop("返程日期"),
        "sort_type": _str_prop("排序方式"), "max_price": _str_prop("最高价")},
        ["origin", "destination"]),
    _s("search_trains", "火车搜索：高铁/普速备选", {
        "origin": _str_prop("出发城市"), "destination": _str_prop("目的地"),
        "dep_date": _str_prop("出发日期 YYYY-MM-DD"), "back_date": _str_prop("返程日期"),
        "seat_class": _str_prop("席别"), "journey_type": _str_prop("行程类型"),
        "sort_type": _str_prop("排序方式")},
        ["origin", "destination"]),
    _s("search_hotels", "酒店/民宿搜索：按价格、POI 距离筛选住宿", {
        "dest_name": _str_prop("目的地城市"), "check_in": _str_prop("入住日期"),
        "check_out": _str_prop("退房日期"), "hotel_types": _str_prop("类型如 民宿,客栈"),
        "max_price": _str_prop("每晚最高价"), "sort": _str_prop("排序"),
        "poi_name": _str_prop("邻近POI"), "hotel_stars": _str_prop("星级")},
        ["dest_name"]),
    _s("search_marriott_hotels", "万豪旗下酒店搜索：高端备选", {
        "dest_name": _str_prop("目的地"), "check_in": _str_prop("入住日期"),
        "check_out": _str_prop("退房日期"), "hotel_brands": _str_prop("品牌"),
        "max_price": _str_prop("最高价"), "sort": _str_prop("排序")},
        ["dest_name"]),
    _s("search_marriott_packages", "万豪套餐搜索：体验产品/接待", {
        "keyword": _str_prop("关键词"), "sort_type": _str_prop("排序")},
        ["keyword"]),
    _s("search_pois", "POI/景点搜索：文化场所、节庆活动", {
        "city_name": _str_prop("城市"), "keyword": _str_prop("关键词"),
        "category": _str_prop("分类"), "poi_level": _str_prop("等级")},
        ["city_name"]),
    _s("search_keyword", "关键词搜索：签证/租车/保险/美食等广义覆盖", {
        "query": _str_prop("查询串")}, ["query"]),
    _s("search_ai", "AI语义搜索：自然语言理解复杂意图（核心能力）", {
        "query": _str_prop("自然语言查询")}, ["query"]),
]

# 追加到 system 的工具使用指引（phase_prompt 之外，指导 LLM 何时调用工具）
_TOOL_GUIDANCE = (
    "工具使用指引：需要真实交通/住宿/景点/文化活动数据时，调用飞猪搜索工具"
    "（search_flights/search_trains/search_hotels/search_pois/search_keyword/search_ai 等）"
    "或聚合能力（profile_field_site 生成田野点画像、compose_itinerary 组合行程）。"
    "文档能力（compose_proposal/compose_interview_guide/suggest_informants）用于产出方案、"
    "访谈提纲与访谈对象候选。不要臆造数据；工具不可用时如实说明并给出可执行的下一步。"
)


def _to_dict(data: Any) -> dict:
    """把包络 data（pydantic/dict/其它）规一为可落盘的 dict。"""
    if hasattr(data, "model_dump"):
        return data.model_dump(mode="json")
    if isinstance(data, dict):
        return dict(data)
    return {"value": data}


def _query_label(name: str, args: dict) -> str:
    """给缓存搜索记录一个可读 query（工具名 + 关键入参）。"""
    parts = [str(v) for v in args.values() if v not in (None, "", [])]
    return f"{name}: " + " ".join(parts[:4]) if parts else name


class FieldAgent:
    """田野罗盘 Agent — 能力内核宿主（无状态业务，编排 kernel 能力）。"""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.repo = JsonRepository(self.config.projects_dir)
        self.flyai = FlyAIClient(self.config.flyai_api_key)
        self.llm: LLMClient | None = None
        if getattr(self.config, "dashscope_available", False):
            try:
                self.llm = LLMClient(self.config.dashscope_api_key, self.config.llm_models)
            except RuntimeError:
                self.llm = None
        self.project_id: str | None = None

    # ─── 项目管理 ──────────────────────────────

    def create_project(self, project_id: str | None = None) -> str:
        pid = project_id or f"field_{datetime.now():%Y%m%d_%H%M%S}"
        self.repo.create(pid)
        self.project_id = pid
        return pid

    def load_project(self, project_id: str) -> str:
        try:
            self.repo.get_state(project_id)
        except KeyError as exc:
            raise FileNotFoundError(f"Project '{project_id}' not found") from exc
        self.project_id = project_id
        return project_id

    def list_projects(self) -> list[dict[str, Any]]:
        return self.repo.list_projects()

    def current_phase(self) -> str | None:
        if not self.project_id:
            return None
        return self.repo.get_phase(self.project_id)

    # ─── 能力分派（统一兜底，绝不裸抛）──────────────

    def client_kernel_dispatch(self, name: str, args: dict) -> Envelope:
        """把一次工具调用分派到能力/搜索原语，任何异常折进 Envelope(ok=False)。

        - registry 能力：先用 args_model 预校验，失败 → code="invalid_arguments"；
          注入 client/llm 后调用，非包络返回值（如 ProposalDoc/dict）包成 ok=True；
          调用期异常（含 compose_proposal 缺渠道 ValueError）→ code="internal_error"。
        - 搜索原语：透传 flyai（其内部已把失败折成包络）；参数不符 TypeError → internal_error。
        - 未知工具名：code="unknown_tool"。
        """
        args = args if isinstance(args, dict) else {}

        if name in _CAPABILITY_DISPATCH:
            impl, inject = _CAPABILITY_DISPATCH[name]
            meta = capabilities().get(name)
            if meta is not None:
                try:
                    validated = meta.args_model.model_validate(args).model_dump()
                except ValidationError as exc:
                    return Envelope(ok=False, error=ToolError(
                        code="invalid_arguments",
                        message=f"参数校验失败：{exc.errors()[0].get('msg', '') if exc.errors() else exc}",
                        hint="请按工具 schema 提供参数"))
            else:  # 理论不应发生：registry 未注册同名能力
                validated = dict(args)
            kw = {"client": self.flyai} if inject == "client" else {"llm": self.llm}
            try:
                result = impl(**validated, **kw)
            except Exception as exc:  # noqa: BLE001 — 统一兜底，异常不外泄给 loop
                return Envelope(ok=False, error=ToolError(
                    code="internal_error", message=str(exc),
                    hint="能力执行异常，可换参数或稍后重试"))
            return result if isinstance(result, Envelope) else Envelope(ok=True, data=result)

        if name in _SEARCH_METHODS:
            method = getattr(self.flyai, name)
            try:
                env = method(**args)
            except Exception as exc:  # noqa: BLE001 — 未知/非法参数兜底
                return Envelope(ok=False, error=ToolError(
                    code="internal_error", message=str(exc),
                    hint="请按搜索工具 schema 提供参数"))
            return env if isinstance(env, Envelope) else Envelope(ok=True, data=env)

        return Envelope(ok=False, error=ToolError(
            code="unknown_tool", message=f"未知工具: {name}", hint="可用: 搜索8原语+registry能力"))

    # ─── 核心对话循环 ──────────────────────────────

    def chat(self, message: str, use_tools: bool = True) -> dict[str, Any]:
        """处理一轮对话。返回 {reply, phase, tool_events, phase_transition}。"""
        if not self.project_id:
            self.create_project()
        pid = self.project_id

        history = self.repo.get_messages(pid, limit=12)
        self._apply_extract(pid, message)
        self.repo.append_message(pid, "user", message)

        phase = self.repo.get_phase(pid)
        if self.llm is not None:
            system = "\n\n".join([phases.phase_prompt(phase), _TOOL_GUIDANCE,
                                  self._snapshot(pid)])
            messages = [{"role": "system", "content": system}]
            messages += [{"role": m["role"], "content": m["content"]}
                         for m in history if m.get("role") in ("user", "assistant")]
            messages.append({"role": "user", "content": message})
            tools = self._build_tools() if use_tools else []
            loop = run_tool_loop(self.llm, messages, self.client_kernel_dispatch,
                                 tools, slot="interview")
            reply = loop.reply
            tool_events = loop.tool_events
        else:
            reply = self._rule_based_reply(phase)
            tool_events = []

        self._persist_events(pid, tool_events)
        self.repo.append_message(pid, "assistant", reply,
                                 {"tool_calls": len(tool_events)})

        transition = self._advance(pid)
        return {
            "reply": reply,
            "phase": self.repo.get_phase(pid),
            "tool_events": tool_events,
            "phase_transition": transition,
        }

    # ─── system 组装与快照 ──────────────────────────────

    def _build_tools(self) -> list[dict]:
        """8 个搜索原语 schema + registry 能力 schema（单一来源 build_tool_schemas）。"""
        return _SEARCH_TOOL_SCHEMAS + build_tool_schemas()

    def _snapshot(self, pid: str) -> str:
        """repo 快照摘要：已采集上下文 + 各渠道计数，帮 LLM 掌握现状不重复问。"""
        state = self.repo.get_state(pid)
        ctx = state.get("research_context") or {}
        filled = [f"{k}={v}" for k, v in ctx.items()
                  if k != "extras" and str(v).strip()]
        extras = ctx.get("extras") or {}
        if extras:
            filled += [f"{k}={v}" for k, v in extras.items()]
        n_sites = len(state.get("field_sites") or [])
        n_inf = len(state.get("informants") or [])
        n_prop = len(state.get("proposals") or [])
        return ("当前项目快照："
                f"已采集[{'；'.join(filled) if filled else '（空）'}]；"
                f"候选田野点 {n_sites} 个、访谈对象 {n_inf} 位、"
                f"方案草稿 {n_prop} 份"
                f"{'；已有行程' if self._has_itinerary(state, pid) else '；尚无行程'}。")

    # ─── 采集：extract 落研究上下文 ──────────────────────────────

    def _apply_extract(self, pid: str, message: str) -> None:
        """纯函数提取 discipline/scale/budget/purpose/extras.region + 地点，落盘。

        topic/time_window/purpose(对话型) 依赖后续访谈轮迭代采集（Task 4/11 裁决），
        本层只做可确定性抽取的软线索合并，且**不覆盖**已填非空字段（保护用户修正）。
        """
        ctx = self.repo.get_research_context(pid)
        found = {**extract_locations(message, ctx), **extract_researcher_info(message)}
        merged = dict(ctx)
        extras = dict(merged.get("extras") or {})
        for key, value in found.items():
            if not value:
                continue
            if key.startswith("extras."):
                extras[key.split(".", 1)[1]] = value
            elif not str(merged.get(key, "")).strip():
                merged[key] = value
        if extras:
            merged["extras"] = extras
        self.repo.update_research_context(pid, merged)

    # ─── 轮末事件落盘 ──────────────────────────────

    def _persist_events(self, pid: str, events: list[dict]) -> None:
        """把成功的工具事件落盘：搜索→cache_search；画像→save_field_site；
        行程→cache_search(tool=compose_itinerary)。失败/未知事件跳过。"""
        for ev in events:
            if not ev.get("ok"):
                continue
            name = ev["tool"]
            data = _to_dict(ev["data"])
            if name in _SEARCH_METHODS:
                self.repo.cache_search(pid, _query_label(name, ev.get("arguments") or {}),
                                       name, data)
            elif name == "profile_field_site":
                self.repo.save_field_site(pid, {
                    "name": data.get("location", ""), "confirmed": False, "profile": data})
            elif name == "compose_itinerary":
                dests = ",".join(data.get("destinations", []) or [])
                self.repo.cache_search(pid, f"itinerary::{dests}", "compose_itinerary", data)

    def _has_itinerary(self, state: dict, pid: str) -> bool:
        """行程可得：state.travel_plan（预留）或缓存里存在 compose_itinerary 记录。"""
        if state.get("travel_plan"):
            return True
        return any(c.get("tool") == "compose_itinerary"
                   for c in self.repo.list_cached_searches(pid))

    # ─── 确定性推进 ──────────────────────────────

    def _advance(self, pid: str) -> dict[str, str] | None:
        """轮末用 check_advance 确定性判定；可推进则落 phase，返回 {from,to}，否则 None。"""
        state = self.repo.get_state(pid)
        ctx = ResearchContext.model_validate(state.get("research_context") or {})
        sites = [FieldSiteRecord.model_validate(s) for s in state.get("field_sites") or []]
        informants = [InformantRecord.model_validate(i) for i in state.get("informants") or []]
        result = phases.check_advance(
            state["phase"], ctx, sites, informants,
            has_itinerary=self._has_itinerary(state, pid),
            has_proposal=self.repo.get_latest_proposal(pid) is not None,
        )
        if result.can_advance and result.to_phase:
            self.repo.set_phase(pid, result.to_phase)
            return {"from": state["phase"], "to": result.to_phase}
        return None

    # ─── 规则回复（无 LLM 降级）─────────────

    def _rule_based_reply(self, phase: str) -> str:
        replies = {
            "research_positioning": (
                "感谢您的描述。为了帮您制定更精准的田野调查旅行方案，我需要了解几个关键信息：\n\n"
                "1. 您的学科背景和研究取向是什么？\n"
                "2. 初步的研究问题或关注焦点是什么？\n"
                "3. 您对田野地点的确定程度如何？\n\n"
                "请尽量详细地告诉我，我会根据您的回答逐步完善方案。"
            ),
            "field_site_profiles": (
                "好的，我来帮您收集候选田野点的详细信息。\n\n请告诉我您关注的目标区域或具体地点名称。"
            ),
            "itinerary_planning": (
                "了解了。现在我来为您规划详细的行程安排。\n\n请告诉我您的出发城市和预期的调研日期。"
            ),
            "proposal_generation": (
                "正在整合所有信息，生成完整的研究方案...\n\n"
                "方案将包含研究背景、方法论、详细行程、预算明细、风险评估等完整章节。"
            ),
        }
        return replies.get(phase, "收到，请继续说明您的需求。")

    # ─── 方案生成（generate 命令接线）─────────────

    def generate_proposal(self) -> dict[str, Any]:
        """整合 repo 上下文调用 compose_proposal 生成方案并 save_proposal。

        渠道不齐时 compose_proposal 抛 ValueError → 返回 {ok:False, error}，不裸抛。
        """
        if not self.project_id:
            return {"ok": False, "error": "没有活跃项目，请先 new 创建"}
        pid = self.project_id
        state = self.repo.get_state(pid)
        ctx = ResearchContext.model_validate(state.get("research_context") or {})
        sites = self.repo.list_field_sites(pid)
        informants = self.repo.list_informants(pid)
        knowledge = self.repo.list_knowledge(pid)
        itinerary = state.get("travel_plan") or self._latest_itinerary(pid)
        try:
            doc = compose_proposal(ctx, sites, itinerary, informants, knowledge,
                                   llm=self.llm)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        rid = self.repo.save_proposal(pid, doc.markdown, kind="proposal", version="draft")
        return {"ok": True, "id": rid, "mode": doc.mode, "sections": len(doc.sections)}

    def _latest_itinerary(self, pid: str) -> dict | None:
        """从搜索缓存里取最近一次 compose_itinerary 的结果 dict（无则 None）。"""
        plan: dict | None = None
        for c in self.repo.list_cached_searches(pid):
            if c.get("tool") != "compose_itinerary":
                continue
            full = self.repo.get_cached_search(c["id"])
            if full and isinstance(full.get("result"), dict):
                plan = full["result"]
        return plan

    # ─── 云同步（保留旧能力，改读 repo）─────────────

    def sync_to_dingtalk(self, message: str = "") -> bool:
        if not self.config.dingtalk_available or not self.project_id:
            return False
        doc = self.repo.get_latest_proposal(self.project_id)
        if not doc:
            return False
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"田野旅行方案 - {self.project_id}",
                "text": f"### 田野旅行方案\n\n{message or '最新研究方案已更新'}\n\n"
                        f"```\n{(doc.get('content') or '')[:2000]}\n```",
            },
        }
        try:
            import json as _json
            import urllib.request
            data = _json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(self.config.dingtalk_webhook, data=data,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return resp.status == 200
        except Exception:  # noqa: BLE001 — 网络/环境失败静默降级
            return False
