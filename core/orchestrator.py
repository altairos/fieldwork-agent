"""
田野罗盘 Agent — 内核宿主（orchestrator）

Task 9 重写：从"关键词路由 + DialogueEngine/FlyAITools/DashScopeChat 旧宿主"
改为**能力内核宿主**。职责仅为组装与编排，一切业务能力来自 ``kernel``，共享
回合循环来自 ``runtime.loop``：

- 组装 ``JsonRepository`` / ``FlyAIClient`` / ``LLMClient``（可选）/ guidance；
- ``client_kernel_dispatch``：把 LLM 的 function calling 分派到 registry 能力
  （搜索类注入 flyai client，聚合/文档类注入 client/llm），一律经 args_model 校验，
  任何异常统一折进包络；
- ``chat``：extract 采集 → 落研究上下文 → 组装 system → run_tool_loop →
  轮末事件落盘 → ``check_advance`` **确定性**判定推进（绝不由 LLM 宣布）；
- 无 LLM 时回落旧 ``_rule_based_reply`` 规则文案路径（降级可用）。

工具 schema **单一来源**：8 搜索原语与聚合/文档能力全部经 ``registry.build_tool_schemas()``
生成（spec §3.1；MCP/Web 共用同一面），宿主不再手搓搜索 schema。

依赖方向：core/orchestrator → runtime → kernel；kernel 绝不反向 import 本模块。
"""
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fieldwork_agent.kernel.aggregation.itinerary import compose_itinerary
from fieldwork_agent.kernel.aggregation.site_profile import profile_field_site
from fieldwork_agent.kernel.aggregation.window import analyze_fieldwork_window
from fieldwork_agent.kernel.documents.informants import suggest_informants
from fieldwork_agent.kernel.documents.proposal import (
    compose_proposal,
    guide_handler,
    proposal_handler,
)
from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.guidance.extract import apply_message_extractions
from fieldwork_agent.kernel.guidance.rule_replies import rule_based_reply
from fieldwork_agent.kernel.llm import LLMClient
from fieldwork_agent.kernel.models import (
    Envelope,
    FieldSiteRecord,
    InformantRecord,
    ResearchContext,
    ToolError,
)
from fieldwork_agent.kernel.registry import (
    build_tool_schemas,
    capabilities,
    validate_tool_args,
)
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.sources.flyai import (
    FlyAIClient,
    search_ai,
    search_flights,
    search_hotels,
    search_keyword,
    search_marriott_hotels,
    search_marriott_packages,
    search_pois,
    search_trains,
)
from fieldwork_agent.runtime.loop import run_tool_loop

from .config import Config

# registry 全局表只供 schema 与 args_model，不经由它裸调：宿主用下表把能力名绑到
# 真实实现与注入方式。inject: "client" → 注入 self.flyai；"llm" → 注入 self.llm；
# 亦可为 tuple 组合（如 ("client", "llm") 双注入，analyze_fieldwork_window 用）。
# 8 搜索原语的 fn 即 flyai 模块级注入式包装（与 profile/compose 同一 client 注入模式）。
# 文档能力的入参含 JSON 形状的 ``ctx``，故绑到 registry 侧 handler（负责 model_validate
# 与统一包络，spec §2.4）；``generate_proposal`` 这类命令式直调才用底层 compose_proposal。
_CAPABILITY_DISPATCH: dict[str, tuple[Callable[..., Any], str | tuple[str, ...]]] = {
    "profile_field_site": (profile_field_site, "client"),
    "compose_itinerary": (compose_itinerary, "client"),
    "analyze_fieldwork_window": (analyze_fieldwork_window, ("client", "llm")),
    "compose_proposal": (proposal_handler, "llm"),
    "compose_interview_guide": (guide_handler, "llm"),
    "suggest_informants": (suggest_informants, "llm"),
    "search_flights": (search_flights, "client"),
    "search_trains": (search_trains, "client"),
    "search_hotels": (search_hotels, "client"),
    "search_marriott_hotels": (search_marriott_hotels, "client"),
    "search_marriott_packages": (search_marriott_packages, "client"),
    "search_pois": (search_pois, "client"),
    "search_keyword": (search_keyword, "client"),
    "search_ai": (search_ai, "client"),
}

# 搜索原语名集：轮末落盘分类用（成功→cache_search）；dispatch 已并入 _CAPABILITY_DISPATCH
_SEARCH_METHODS: frozenset[str] = frozenset({
    "search_flights", "search_trains", "search_hotels", "search_marriott_hotels",
    "search_marriott_packages", "search_pois", "search_keyword", "search_ai",
})

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
        """把一次工具调用分派到 registry 能力，任何异常折进 Envelope(ok=False)。

        搜索原语与聚合/文档能力走同一条路径（schema 单一来源）：
        - 先用 ``args_model`` 预校验，失败 → code="invalid_arguments"；
        - 注入 client/llm 后调用；能力已是包络（全部内核能力均然）则**直用**（幂等，
          不二次包裹），非包络返回值才包成 ok=True 兜底；
        - 调用期异常 → code="internal_error"（文档能力的缺渠道由 handler 自己折成
          code="missing_channel" 包络，不走此分支）；
        - 未知工具名 → code="unknown_tool"。
        """
        args = args if isinstance(args, dict) else {}

        if name in _CAPABILITY_DISPATCH:
            impl, inject = _CAPABILITY_DISPATCH[name]
            meta = capabilities().get(name)
            if meta is not None:
                validated, err_msg = validate_tool_args(meta, args)
                if validated is None:
                    return Envelope(ok=False, error=ToolError(
                        code="invalid_arguments",
                        message=f"参数校验失败：{err_msg}",
                        hint="请按工具 schema 提供参数"))
            else:  # 理论不应发生：registry 未注册同名能力
                validated = dict(args)
            injects = inject if isinstance(inject, tuple) else (inject,)
            kw = {**({"client": self.flyai} if "client" in injects else {}),
                  **({"llm": self.llm} if "llm" in injects else {})}
            try:
                result = impl(**validated, **kw)
            except Exception as exc:  # noqa: BLE001 — 统一兜底，异常不外泄给 loop
                return Envelope(ok=False, error=ToolError(
                    code="internal_error", message=str(exc),
                    hint="能力执行异常，可换参数或稍后重试"))
            return result if isinstance(result, Envelope) else Envelope(ok=True, data=result)

        return Envelope(ok=False, error=ToolError(
            code="unknown_tool", message=f"未知工具: {name}",
            hint="可用: 搜索8原语+registry能力"))

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
            reply = rule_based_reply(phase)
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
        """工具 schema 单一来源：registry 全部能力（含 8 搜索原语）经 build_tool_schemas 生成。"""
        return build_tool_schemas()

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
        ctx = self.repo.get_research_context(pid)
        found = apply_message_extractions(message, ctx)
        if not found:
            return
        merged = dict(ctx)
        extras = dict(merged.get("extras") or {})
        for key, value in found.items():
            if key.startswith("extras."):
                extras[key.split(".", 1)[1]] = value
            else:
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

    def advance_phase(self) -> dict[str, str] | None:
        """公开推进入口（F04）：确认等写操作后由宿主显式调用，
        对齐 Web 宿主的 ``check_and_advance`` 用法。无活跃项目返回 None。"""
        if not self.project_id:
            return None
        return self._advance(self.project_id)

    def _advance(self, pid: str) -> dict[str, str] | None:
        """轮末用 check_advance 确定性判定；可推进则落 phase，返回 {from,to}，否则 None。"""
        state = self.repo.get_state(pid)
        ctx = ResearchContext.model_validate(state.get("research_context") or {})
        sites = [FieldSiteRecord.model_validate(s) for s in state.get("field_sites") or []]
        informants = [InformantRecord.model_validate(i) for i in state.get("informants") or []]
        # P2-6：D→finished 须显式传确认态（草稿存在≠用户认可），与 web 宿主对齐；
        # stale 确认（依赖已变/有新草稿）不算已确认
        confirmed = (bool(state.get("proposal_confirmed"))
                     and not self.repo.is_proposal_confirmation_stale(pid))
        result = phases.check_advance(
            state["phase"], ctx, sites, informants,
            has_itinerary=self._has_itinerary(state, pid),
            has_proposal=self.repo.get_latest_proposal(pid) is not None,
            proposal_confirmed=confirmed,
        )
        if result.can_advance and result.to_phase:
            self.repo.set_phase(pid, result.to_phase)
            return {"from": state["phase"], "to": result.to_phase}
        return None

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
            full = self.repo.get_cached_search(pid, c["id"])
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
