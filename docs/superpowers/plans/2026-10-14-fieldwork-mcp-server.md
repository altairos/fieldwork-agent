# Fieldwork MCP Server（Plan 2/5）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已落地的能力内核（13 个注册能力 + ProjectRepository）封装为细粒度 MCP Server：22 个 tools / 10 类 resources / 2 个 prompts，stdio 传输，供 WorkBuddy/千问办公等宿主接入。

**Architecture:** MCP Server 是薄壳——schema 翻译 + 参数校验 + repository 接线，零业务逻辑（spec §2）。工具 schema 单一来源 `kernel.build_tool_schemas()` 转译为 `mcp.types.Tool`；状态类 9 工具由 Server 组合 repository+guidance 实现（kernel 不碰 repo 的公理约束的是 kernel 内部，Server 作为宿主有权接线）。

**Tech Stack:** Python ≥3.10、`mcp` 官方 SDK（optional extra）、pydantic v2、pytest（全 mock，不联网，CI 可跑）。

**Spec:** `docs/superpowers/specs/2026-10-14-fieldwork-agent-mcp-kernel-design.md` §2（清单/约定）+ §6 L3（契约测试）。前置：Plan 1 已全部完成（main @ 57a2102），内核 13 能力面已注册。

**后续计划（本计划不含）:** Web 宿主（Plan 3，含 R4 回合协议）、React 前端（Plan 4）、SQLite（Plan 5）。

## Global Constraints

- 依赖方向：`mcp_server/` 可以 import `kernel/` 与 `core.config`；kernel **不得** import mcp SDK（spec §1 单向依赖，L3 测试钉死）。
- 返回包络：所有 tool 结果为 JSON 文本 `{"ok":…, "data":…, "error":…, "degraded":…}`；flyai CLI 缺失 → `ok:false, code:"flyai_unavailable"`，不抛协议异常（spec §2.4 + R5：体验模式 systemMessage 已由内核透传 degraded，Server 不再加工）。
- `project_id` 只出现在状态类与 compose_proposal 的数据装载里；搜索/聚合/文档类不带（spec §2.4）。
- annotations：①②组（搜索+聚合）`readOnlyHint=true + openWorldHint=true`；③文档组与④状态组 `readOnlyHint=false`（spec §2.4 语义按实现微调：suggest/compose 无外部世界调用则 openWorldHint=false）。
- 测试全 mock：不联网、不真实调 flyai、不真实调 DashScope；repo 用 tmp_path。
- ruff `E,F,I,UP,B`；注释中文；Windows git 用 `& "C:\Program Files\Git\cmd\git.exe"`。
- 包注册：新目录 `mcp_server/` 需在 `pyproject.toml [tool.setuptools] packages` 登记 `fieldwork_agent.mcp_server`。

## 文件结构

```
mcp_server/
├── __init__.py       # docstring + create_server 再导出
├── mapping.py        # OpenAI tools schema → mcp.types.Tool 转译 + annotations 规则
├── dispatch.py       # call_tool 分派：kernel 能力(13) / 状态工具(9)，统一包络
├── state_tools.py    # 9 个状态工具的 handler（组合 repo + guidance）
├── resources.py      # 10 类 resource 模板与渲染
├── prompts.py        # fieldwork_interview / refine_proposal_section
├── server.py         # create_server() 组装 + run_stdio()
└── __main__.py       # python -m fieldwork_agent.mcp_server
tests/
├── test_mcp_mapping.py
├── test_mcp_dispatch.py
├── test_mcp_state_tools.py
├── test_mcp_resources_prompts.py
└── test_mcp_server_contract.py   # L3：schema 同构 + in-memory 冒烟
demo_mcp_client.py    # 手动演示脚本（不进 CI）
```

---

### Task 1: 依赖 + mapping.py（schema 转译与 annotations）

**Files:**
- Modify: `pyproject.toml`（optional-dependencies + console script + packages）
- Create: `mcp_server/__init__.py`, `mcp_server/mapping.py`
- Test: `tests/test_mcp_mapping.py`

**Interfaces:**
- Consumes: `fieldwork_agent.kernel.build_tool_schemas()`（OpenAI 格式 `{"type":"function","function":{name,description,parameters}}`）、`fieldwork_agent.kernel.registry.capabilities()` → `dict[str, CapabilityMeta]`（字段 `name/description/fn/args_model/read_only/open_world`；注意 `kernel/__init__` 导出别名为 `CAPABILITIES`）
- Produces: `mapping.capability_tools() -> list[mcp.types.Tool]`（13 个，含 annotations）；`mapping.state_tool(t: dict) -> mcp.types.Tool` 不需要——状态工具在 Task 3 直接以 mcp.types.Tool 手写；`mapping.TOOL_GROUPS: dict[str, str]`（工具名→组标签 `search/aggregation/document`，用于 annotations 与测试）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_mapping.py
from mcp.types import Tool

from fieldwork_agent.mcp_server.mapping import TOOL_GROUPS, capability_tools


def test_capability_tools_are_mcp_tools_with_schema():
    tools = capability_tools()
    assert len(tools) == 13 and all(isinstance(t, Tool) for t in tools)
    flight = next(t for t in tools if t.name == "search_flights")
    assert flight.description
    assert flight.inputSchema["type"] == "object"
    assert "origin" in flight.inputSchema["properties"]


def test_annotations_groups():
    tools = {t.name: t for t in capability_tools()}
    assert tools["search_hotels"].annotations.readOnlyHint is True
    assert tools["search_ai"].annotations.openWorldHint is True
    assert tools["profile_field_site"].annotations.readOnlyHint is True
    # 文档组：无外部世界调用（llm 由注入决定），非只读
    assert tools["compose_proposal"].annotations.readOnlyHint is False
    assert tools["suggest_informants"].annotations.readOnlyHint is False
    assert {"search_flights", "compose_itinerary", "compose_proposal",
            "profile_field_site"} <= set(TOOL_GROUPS)
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_mcp_mapping.py -v` → ModuleNotFoundError

- [ ] **Step 3: 实现** — `pyproject.toml`：`mcp = ["mcp>=1.2"]` 加入 optional-dependencies；`[project.scripts]` 加 `fieldwork-mcp = "fieldwork_agent.mcp_server.server:run_stdio"`；packages 追加 `"fieldwork_agent.mcp_server"`。安装：`pip install -e ".[dev,mcp]"`。mapping.py：

```python
# mcp_server/mapping.py
"""kernel OpenAI 格式 tools schema → mcp.types.Tool 转译（spec §2 schema 单一来源的 MCP 侧）。"""
from mcp.types import Tool, ToolAnnotations

# 注意：kernel/__init__ 把 capabilities 重命名为 CAPABILITIES，这里从 registry 直导原函数
from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.registry import capabilities

# 组标签：决定 annotations；与注册名一一对应，测试钉死
TOOL_GROUPS: dict[str, str] = {
    **{n: "search" for n in ("search_flights", "search_trains", "search_hotels",
                             "search_pois", "search_keyword", "search_ai",
                             "search_marriott_hotels", "search_marriott_packages")},
    "profile_field_site": "aggregation",
    "compose_itinerary": "aggregation",
    "compose_proposal": "document",
    "compose_interview_guide": "document",
    "suggest_informants": "document",
}


def _annotations(name: str) -> ToolAnnotations:
    group = TOOL_GROUPS.get(name, "document")
    if group in ("search", "aggregation"):
        return ToolAnnotations(readOnlyHint=True, openWorldHint=True)
    return ToolAnnotations(readOnlyHint=False, openWorldHint=False)


def capability_tools() -> list[Tool]:
    metas = capabilities()
    tools = []
    for entry in build_tool_schemas():
        fn = entry["function"]
        tools.append(Tool(name=fn["name"], description=fn["description"],
                          inputSchema=fn["parameters"],
                          annotations=_annotations(fn["name"])))
    # 防漂移：注册表与 schema 清单必须全等（单一来源的两面）
    assert {t.name for t in tools} == set(metas), "注册表与 schema 清单漂移"
    return tools
```

- [ ] **Step 4: 确认通过** — `pytest tests/test_mcp_mapping.py -v` → PASS
- [ ] **Step 5: Commit** — `git add mcp_server tests/test_mcp_mapping.py pyproject.toml; git commit -m "feat(mcp): 依赖与能力工具schema转译"`

---

### Task 2: dispatch.py 能力分派 + kernel 文档 handler 补 llm 注入口

**Files:**
- Create: `mcp_server/dispatch.py`
- Modify: `kernel/documents/proposal.py`（`_proposal_handler` 增 `llm` 透传）、`kernel/documents/informants.py`（同）、`pyproject.toml`（如需要 context 键约定则不动）
- Test: `tests/test_mcp_dispatch.py`

**Interfaces:**
- Consumes: `capabilities()`、各 `CapabilityMeta.fn`（搜索/聚合 handler 收裸参数返回 Envelope；文档 handler 收 dict ctx 返回包络 Envelope——0c5b7bb 已统一）、`RepositoryLike`（Task 3 的 state handler 不经这里）
- Produces: `dispatch.call_capability(name: str, arguments: dict, ctx: dict) -> dict`（JSON 可序列化的包络 dict；ctx 可含 `"llm"`，仅当目标函数签名含 `llm` 参数时注入）；`dispatch.envelope_dict(env: Envelope) -> dict`；`dispatch.UNKNOWN_TOOL/INVALID_ARGS` 错误码常量。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_dispatch.py
import pytest

from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.mcp_server import dispatch


class Echo:
    def __init__(self): self.got = None
    def search_hotels(self, dest_name, **kw):
        self.got = (dest_name, kw)
        return Envelope(ok=True, data={"echo": dest_name})


def test_call_capability_validates_and_trims_kwargs(monkeypatch):
    monkeypatch.setitem(dispatch._CLIENTLESS_SINGLETONS, "search_hotels",
                        Echo().search_hotels)   # 表内存可调用 handler，不是对象
    out = dispatch.call_capability("search_hotels", {"dest_name": "大理",
                                                     "hotel_types": "民宿"}, ctx={})
    assert out["ok"] is True and out["data"]["echo"] == "大理"


def test_unknown_tool_envelope():
    out = dispatch.call_capability("nope", {}, ctx={})
    assert out["ok"] is False and out["error"]["code"] == dispatch.UNKNOWN_TOOL


def test_invalid_args_envelope():
    out = dispatch.call_capability("search_hotels", {"dest_name": 1, "hotel_types": None}, ctx={})
    # pydantic 允许 int→str 强转；缺参才算 invalid：
    out2 = dispatch.call_capability("search_hotels", {}, ctx={})
    assert out2["ok"] is False and out2["error"]["code"] == dispatch.INVALID_ARGS


def test_llm_injected_only_when_accepted(monkeypatch):
    calls = {}
    def fake_proposal(**kw):
        calls.update(kw)
        return Envelope(ok=True, data={"markdown": "x"})
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "compose_proposal", fake_proposal)
    sentinel = object()
    dispatch.call_capability("compose_proposal", {"ctx": {}, "sites": [],
                                                  "informants": [{"code": "P1"}],
                                                  "itinerary": {"legs": []},
                                                  "knowledge": []}, ctx={"llm": sentinel})
    assert calls["llm"] is sentinel  # 签名含 llm 才注入
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_mcp_dispatch.py -v`

- [ ] **Step 3: 实现** — dispatch.py：

```python
# mcp_server/dispatch.py
"""call_tool 分派：kernel 能力 → 统一包络 dict（spec §2.4；业务失败走包络不走协议异常）。"""
import inspect
from typing import Any

# capabilities 从 registry 直导（kernel/__init__ 导出的别名是 CAPABILITIES）
from fieldwork_agent.kernel.models import Envelope
from fieldwork_agent.kernel.registry import capabilities

UNKNOWN_TOOL = "unknown_tool"
INVALID_ARGS = "invalid_args"
INTERNAL = "internal_error"

# 分派表在模块级构建，测试可 monkeypatch 替换为替身（表内一律存 callable handler）
_CAPABILITY_FNS = {name: meta.fn for name, meta in capabilities().items()}
# 搜索原语的共享 FlyAIClient 版本 handler；由 server 组装时注入（Task 5）
_CLIENTLESS_SINGLETONS: dict[str, Any] = {}


def envelope_dict(env: Envelope) -> dict:
    return env.model_dump(mode="json", exclude_none=True)


def _error(code: str, message: str, hint: str = "") -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def call_capability(name: str, arguments: dict, ctx: dict) -> dict:
    fn = _CLIENTLESS_SINGLETONS.get(name) or _CAPABILITY_FNS.get(name)
    if fn is None:
        return _error(UNKNOWN_TOOL, f"未知工具: {name}",
                      "先 list_tools 获取可用工具名")
    try:
        if "llm" in inspect.signature(fn).parameters:
            arguments = {**arguments, "llm": ctx.get("llm")}
        result = fn(**arguments)
    except (TypeError, ValueError) as exc:   # pydantic 校验与缺参/多参统一为 invalid_args
        return _error(INVALID_ARGS, f"参数校验失败: {exc}",
                      "对照该工具 inputSchema 修正参数")
    except Exception as exc:
        return _error(INTERNAL, f"执行异常: {exc}")
    if isinstance(result, Envelope):
        return envelope_dict(result)
    return {"ok": True, "data": result}
```

kernel 侧修改：`_proposal_handler` 与 informants 的注册 handler 各加 `llm: Any = None` keyword 参数并透传给 `compose_proposal/compose_interview_guide/suggest_informants`（宿主不传即 None，走模板降级——与 spec "无 key 降级"一致）。同时为该改动在 `tests/test_documents.py` 补一条：`_proposal_handler(..., llm=FakeLLM(...))` 时走 LLM 分支。搜索原语的注册 handler 若内部自建 `FlyAIClient()` 则不动；server 组装（Task 5）负责替换 `_CLIENTLESS_SINGLETONS` 为共享客户端版本。

- [ ] **Step 4: 确认通过** — `pytest tests/test_mcp_dispatch.py tests/test_documents.py -v`；全量 `pytest -q` 无回归
- [ ] **Step 5: Commit** — `git commit -am "feat(mcp): call_tool分派与统一包络;文档能力开放llm注入"`

---

### Task 3: 状态工具 9 个（repository 接线）

**Files:**
- Create: `mcp_server/state_tools.py`
- Test: `tests/test_mcp_state_tools.py`

**Interfaces:**
- Consumes: `ProjectRepository` Protocol 23 方法（`kernel/repository/protocol.py`）、`JsonRepository(projects_dir: Path)`、`guidance.phases.check_advance/REQUIRED_FIELDS/PHASES`、`kernel.models.ResearchContext/FieldSiteRecord/InformantRecord`
- Produces: `state_tools.build_handlers(repo: ProjectRepository) -> dict[str, Callable[..., dict]]`——键为 9 个工具名，值为收 `**arguments` 返回包络 dict 的 handler；`state_tools.STATE_TOOLS: list[mcp.types.Tool]`（9 个手写 Tool 定义，name/description/inputSchema 与 handler 参数一致）。工具名与参数：`create_project(project_id?)`（缺省生成 `field_<时间戳>`）、`list_projects()`、`get_project(project_id)`、`update_research_context(project_id, patch)`、`save_field_site(project_id, record)`、`save_informant(project_id, record)`、`add_knowledge_entry(project_id, title, content, source?, tags?, category?)`、`search_knowledge(project_id, text)`、`check_phase_advance(project_id)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_state_tools.py
from mcp.types import Tool

from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server.state_tools import STATE_TOOLS, build_handlers


def make_repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


def test_state_tools_definitions_cover_nine():
    assert len(STATE_TOOLS) == 9 and all(isinstance(t, Tool) for t in STATE_TOOLS)
    assert {"create_project", "check_phase_advance", "search_knowledge"} \
        <= {t.name for t in STATE_TOOLS}


def test_create_and_get_roundtrip(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    out = h["create_project"]({"project_id": "p1"})
    assert out["ok"] and out["data"]["project_id"] == "p1"
    got = h["get_project"]({"project_id": "p1"})
    assert got["data"]["phase"] == "research_positioning"


def test_update_context_and_advance(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    h["create_project"]({"project_id": "p1"})
    h["update_research_context"]({"project_id": "p1", "patch": {
        "discipline": "人类学", "topic": "宗族", "scale": "单人", "budget": "2万"}})
    r = h["check_phase_advance"]({"project_id": "p1"})
    assert r["ok"] and r["data"]["can_advance"] is True
    assert r["data"]["to_phase"] == "field_site_profiles"


def test_unknown_project_is_error_not_crash(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    out = h["get_project"]({"project_id": "ghost"})
    assert out["ok"] is False and out["error"]["code"] == "project_not_found"


def test_save_site_and_informant(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    h["create_project"]({"project_id": "p1"})
    assert h["save_field_site"]({"project_id": "p1",
                                 "record": {"name": "喜洲", "confirmed": True}})["ok"]
    assert h["save_informant"]({"project_id": "p1",
                                "record": {"code": "P1", "type": "长老"}})["ok"]
    r = h["check_phase_advance"]({"project_id": "p1"})
    assert r["data"]["can_advance"] is True   # confirmed site + ≥1 informant → B→C 由宿主 set_phase 落地


def test_knowledge_add_search(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    h["create_project"]({"project_id": "p1"})
    h["add_knowledge_entry"]({"project_id": "p1", "title": "族谱材料",
                              "content": "明代以降宗族碑刻著录", "tags": ["碑刻"]})
    hits = h["search_knowledge"]({"project_id": "p1", "text": "碑刻"})
    assert hits["ok"] and len(hits["data"]) == 1
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_mcp_state_tools.py -v`
- [ ] **Step 3: 实现** — handler 统一模板：

```python
# mcp_server/state_tools.py
"""9 个状态工具：Server 组合 repository + guidance（spec §2 前言的宿主接线权）。"""
from datetime import datetime
from typing import Any, Callable

from mcp.types import Tool

from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.models import (FieldSiteRecord, InformantRecord,
                                           ResearchContext)
from fieldwork_agent.kernel.repository.protocol import ProjectRepository

ITINERARY_STATE_KEYS = ("itinerary", "travel_plan")   # 兼容旧键


def _ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def _err(code: str, message: str, hint: str = "") -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def build_handlers(repo: ProjectRepository) -> dict[str, Callable[..., dict]]:
    def create_project(project_id: str | None = None) -> dict:
        pid = project_id or f"field_{datetime.now():%Y%m%d_%H%M%S}"
        repo.create(pid)
        return _ok({"project_id": pid})

    def list_projects() -> dict:
        return _ok(repo.list_projects())

    def get_project(project_id: str) -> dict:
        try:
            return _ok(repo.get_state(project_id))
        except KeyError:
            return _err("project_not_found", f"项目不存在: {project_id}",
                        "先 create_project 或使用 list_projects 查已有项目")

    def update_research_context(project_id: str, patch: dict) -> dict:
        repo.update_research_context(project_id, patch)
        return _ok(repo.get_research_context(project_id))

    def save_field_site(project_id: str, record: dict) -> dict:
        return _ok({"id": repo.save_field_site(project_id, record)})

    def save_informant(project_id: str, record: dict) -> dict:
        return _ok({"id": repo.save_informant(project_id, record)})

    def add_knowledge_entry(project_id: str, title: str, content: str,
                            source: str = "user", tags: list[str] | None = None,
                            category: str = "general") -> dict:
        return _ok({"id": repo.add_knowledge(project_id, title, content,
                                             source=source, tags=tags,
                                             category=category)})

    def search_knowledge(project_id: str, text: str) -> dict:
        return _ok(repo.search_knowledge(project_id, text))

    def check_phase_advance(project_id: str) -> dict:
        try:
            state = repo.get_state(project_id)
        except KeyError:
            return _err("project_not_found", f"项目不存在: {project_id}")
        ctx = ResearchContext.model_validate(repo.get_research_context(project_id))
        sites = [FieldSiteRecord.model_validate(s) for s in repo.list_field_sites(project_id)]
        informants = [InformantRecord.model_validate(i) for i in repo.list_informants(project_id)]
        has_itinerary = any(state.get(k) for k in ITINERARY_STATE_KEYS)
        result = phases.check_advance(
            state["phase"], ctx, sites, informants,
            has_itinerary, bool(state.get("proposals")),
            bool(state.get("proposal_confirmed")))
        return _ok(result.model_dump())

    return {"create_project": create_project, "list_projects": list_projects,
            "get_project": get_project, "update_research_context": update_research_context,
            "save_field_site": save_field_site, "save_informant": save_informant,
            "add_knowledge_entry": add_knowledge_entry,
            "search_knowledge": search_knowledge,
            "check_phase_advance": check_phase_advance}
```

`STATE_TOOLS: list[Tool]` 按上述签名手写 9 个定义（description 说明状态语义与 project_id 约束；inputSchema 与各 handler 参数严格一致，例如 `update_research_context` 为 `{"type":"object","properties":{"project_id":{"type":"string"},"patch":{"type":"object"}},"required":["project_id","patch"]}`；annotations 一律 `readOnlyHint=False, destructiveHint=False`——upsert 幂等语义由 Protocol 保证）。每个 handler 外层用 try/except KeyError 兜底成 `project_not_found`（update/save/add 同样适用，实现时逐一包裹，不遗漏）。

- [ ] **Step 4: 确认通过** — `pytest tests/test_mcp_state_tools.py -v`
- [ ] **Step 5: Commit** — `git commit -am "feat(mcp): 9状态工具接线repository并组合check_advance"`

---

### Task 4: Resources（10 类）+ Prompts（2 个）

**Files:**
- Create: `mcp_server/resources.py`, `mcp_server/prompts.py`
- Test: `tests/test_mcp_resources_prompts.py`

**Interfaces:**
- Consumes: `guidance.phases`（`PHASES/phase_prompt/guiding_questions/REQUIRED_FIELDS`）、`guidance.informants`（`INFORMANT_EXAMPLES/get_informant/INTERVIEW_GUIDE_METHODOLOGY`）、`documents.proposal.PROPOSAL_SECTIONS`、`ProjectRepository`
- Produces: `resources.list_resources(repo) -> list[Resource]`（静态 7 项 + 项目类按存量展开）；`resources.read_resource(repo, uri: str) -> dict`（`{"ok", "text"|"error"}`）；`prompts.list_prompts() -> list[Prompt]`；`prompts.get_prompt(name, args) -> dict`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_resources_prompts.py
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server import prompts, resources


def make_repo(tmp_path):
    r = JsonRepository(projects_dir=tmp_path / "projects")
    r.create("p1")
    r.update_research_context("p1", {"topic": "宗族变迁", "purpose": "global"})
    r.save_field_site("p1", {"name": "喜洲", "confirmed": True})
    return r


def test_static_resource_catalog():
    uris = {r.uri for r in resources.STATIC_RESOURCES}
    assert "fieldwork://guidance/phases" in uris
    assert "fieldwork://guidance/questions/research_positioning" in uris
    assert "fieldwork://guidance/checklists/itinerary_planning" in uris
    assert "fieldwork://guidance/informants/library" in uris
    assert "fieldwork://guidance/interview-guide/methodology" in uris
    assert "fieldwork://templates/proposal-outline" in uris
    # 示范词条按库展开
    assert "fieldwork://guidance/informants/长老" in uris


def test_read_phases_resource_mentions_advance(tmp_path):
    out = resources.read_resource(make_repo(tmp_path), "fieldwork://guidance/phases")
    assert out["ok"] and "proposal_confirmation" in out["text"]  # 三态契约随协议分发


def test_project_resources_expand_with_repo(tmp_path):
    repo = make_repo(tmp_path)
    items = resources.list_resources(repo)
    uris = {r.uri for r in items}
    # 项目族三件套（spec §2.2 的 10 类中占 3 类，按存量展开）
    assert {"fieldwork://project/p1/summary", "fieldwork://project/p1/sites",
            "fieldwork://project/p1/informants"} <= uris
    got = resources.read_resource(repo, "fieldwork://project/p1/summary")
    assert got["ok"] and "宗族变迁" in got["text"]
    bad = resources.read_resource(repo, "fieldwork://project/ghost/summary")
    assert bad["ok"] is False and bad["error"]["code"] == "project_not_found"


def test_informants_library_resource_has_examples():
    out = resources.read_resource(make_repo(tmp_path),
                                  "fieldwork://guidance/informants/library")
    assert out["ok"] and "参考起点" in out["text"]   # 明示"非枚举白名单"


def test_prompts_catalog():
    names = {p.name for p in prompts.list_prompts()}
    assert names == {"fieldwork_interview", "refine_proposal_section"}
    got = prompts.get_prompt("fieldwork_interview", {"phase": "field_site_profiles"})
    assert got["ok"] and "田野点画像" in got["messages"][0]["content"] or "引导" in str(got)
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_mcp_resources_prompts.py -v`
- [ ] **Step 3: 实现** — resources.py：`STATIC_RESOURCES: list[Resource]` = 固定 URI 全集：`guidance/phases`（渲染：PHASES 顺序 + 各阶段 REQUIRED_FIELDS + check_advance 四条件文字，含 `proposal_confirmation` 与 `interviews_not_applicable` 说明——从 `phases` 模块常量拼装，禁止复制第二份事实）、`guidance/questions/{p}` 与 `guidance/checklists/{p}`（×5 阶段，分别渲染 `phases.guiding_questions(p)` 与 `REQUIRED_FIELDS[p]`）、`guidance/informants/library`（示范词条全量 markdown + "参考起点，非枚举白名单"声明）、`guidance/informants/{type}`（×INFORMANT_EXAMPLES 键，单词条完整档案）、`guidance/interview-guide/methodology`（INTERVIEW_GUIDE_METHODOLOGY 原文）、`templates/proposal-outline`（PROPOSAL_SECTIONS 八章 + 各章一句写作要点）；`list_resources(repo)` = 静态 + 对 `repo.list_projects()` 每个项目展开 `project/{id}/summary`、`project/{id}/sites`、`project/{id}/informants` 三件套；`read_resource` 按 URI 路由渲染，项目类 KeyError → `project_not_found`。prompts.py：`fieldwork_interview`（args: phase，system=`phases.phase_prompt(phase)` + 首行提示宿主先读 `fieldwork://guidance/phases` 与 `questions/{phase}`）；`refine_proposal_section`（args: section，system 要求只输出该章 markdown）。返回 `{"ok": True, "description":…, "messages":[{"role":"user","content":…}]}`。

- [ ] **Step 4: 确认通过** — `pytest tests/test_mcp_resources_prompts.py -v`
- [ ] **Step 5: Commit** — `git commit -am "feat(mcp): guidance资源族与访谈prompt模板随协议分发"`

---

### Task 5: server.py 组装 + L3 契约测试 + demo client

**Files:**
- Create: `mcp_server/server.py`, `mcp_server/__main__.py`, `demo_mcp_client.py`
- Modify: `mcp_server/__init__.py`（再导出 create_server）
- Test: `tests/test_mcp_server_contract.py`

**Interfaces:**
- Consumes: Task 1-4 全部 + `core.config.Config`（projects_dir / dashscope_available / flyai_api_key）+ `kernel.llm.LLMClient`
- Produces: `server.create_server(config: Config | None = None, repo: ProjectRepository | None = None) -> mcp.server.Server`；`server.run_stdio()`（console script 入口）；工具总数 22（13 能力 + 9 状态）。

- [ ] **Step 1: 写失败测试（L3 三件套）**

```python
# tests/test_mcp_server_contract.py
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server.server import create_server

EXPECTED = 22


@pytest.fixture
def server(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    return create_server(config=None, repo=repo)   # config=None → 无 LLM、搜索走真实替身


async def test_schema_isomorphism(server):
    """L3①：MCP 注册的工具名+inputSchema 与 kernel build_tool_schemas() 逐一对齐。"""
    async with create_connected_server_and_client_session(server) as client:
        tools = (await client.list_tools()).tools
    assert len(tools) == EXPECTED
    kernel_side = {t["function"]["name"]: t["function"]["parameters"]
                   for t in build_tool_schemas()}
    for t in tools:
        if t.name in kernel_side:
            assert t.inputSchema == kernel_side[t.name], f"{t.name} schema 漂移"
    assert {t.name for t in tools} - set(kernel_side) == {
        "create_project", "list_projects", "get_project", "update_research_context",
        "save_field_site", "save_informant", "add_knowledge_entry",
        "search_knowledge", "check_phase_advance"}


async def test_state_tool_roundtrip(server):
    async with create_connected_server_and_client_session(server) as client:
        res = await client.call_tool("create_project", {"project_id": "p1"})
        payload = json.loads(res.content[0].text)
        assert payload["ok"] and payload["data"]["project_id"] == "p1"


async def test_search_degrades_without_cli(server, monkeypatch):
    async with create_connected_server_and_client_session(server) as client:
        res = await client.call_tool("search_hotels", {"dest_name": "大理"})
        payload = json.loads(res.content[0].text)
    # 测试环境无 flyai CLI：必须走统一包络降级而非协议异常
    assert payload["ok"] is False
    assert payload["error"]["code"] in ("flyai_unavailable", "source_exception")


async def test_resources_and_prompts_visible(server):
    async with create_connected_server_and_client_session(server) as client:
        assert (await client.list_resources()).resources
        got = await client.read_resource("fieldwork://guidance/phases")
        assert "proposal_confirmation" in got.contents[0].text
        assert len((await client.list_prompts()).prompts) == 2
```

（测试文件顶部补 `import json`；pytest-asyncio 若未配置则在 `pyproject [tool.pytest.ini_options]` 加 `asyncio_mode = "auto"` 并把 `pytest-asyncio` 加进 dev extra。）

- [ ] **Step 2: 确认失败** — `pytest tests/test_mcp_server_contract.py -v`
- [ ] **Step 3: 实现** — server.py：

```python
# mcp_server/server.py
"""MCP Server 组装（spec §2 薄壳）：零业务逻辑，只做 list/read/call 三路路由。"""
import anyio
from mcp.server import Server
from mcp.types import TextContent

from fieldwork_agent.mcp_server import prompts as prompts_mod
from fieldwork_agent.mcp_server import resources as res_mod
from fieldwork_agent.mcp_server import state_tools
from fieldwork_agent.mcp_server.dispatch import call_capability
from fieldwork_agent.mcp_server.mapping import capability_tools


def create_server(config=None, repo=None) -> Server:
    if repo is None:
        from fieldwork_agent.core.config import Config
        from fieldwork_agent.kernel.repository.json_repo import JsonRepository
        cfg = config or Config()
        repo = JsonRepository(projects_dir=cfg.projects_dir)
    else:
        cfg = config
    handlers = state_tools.build_handlers(repo)
    tools = capability_tools() + handlers_to_tools(handlers, state_tools.STATE_TOOLS)
    srv: Server = Server("fieldwork-agent")
    llm = None
    if cfg is not None and getattr(cfg, "dashscope_available", False):
        from fieldwork_agent.kernel.llm import LLMClient
        llm = LLMClient(api_key=cfg.dashscope_api_key, models=cfg.llm_models)
    ctx = {"llm": llm}

    @srv.list_tools()
    async def _list_tools() -> list:
        return tools

    @srv.call_tool()
    async def _call_tool(name: str, arguments: dict):
        if name in handlers:
            payload = handlers[name](**arguments)
        else:
            payload = await anyio.to_thread.run_sync(
                lambda: call_capability(name, arguments, ctx))
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]

    @srv.list_resources()
    async def _list_resources():
        return res_mod.list_resources(repo)

    @srv.read_resource(uri)
    async def _read_resource(uri):
        out = res_mod.read_resource(repo, str(uri))
        if not out["ok"]:
            raise ValueError(out["error"]["message"])
        return out["text"]

    @srv.list_prompts()
    async def _list_prompts():
        return prompts_mod.list_prompts()

    @srv.get_prompt()
    async def _get_prompt(name: str, arguments: dict):
        return prompts_mod.get_prompt(name, arguments or {})

    return srv


def run_stdio():
    """console script 入口：fieldwork-mcp（stdio 传输）。"""
    import asyncio

    import mcp.types as types
    from mcp.server.stdio import stdio_server
    from mcp.server.models import InitializationOptions

    srv = create_server()

    async def _amain() -> None:
        init_options = InitializationOptions(
            server_name="fieldwork-agent",
            server_version="0.1.0",
            capabilities=srv.get_capabilities(
                notification_options=types.NotificationOptions(),
                experimental_capabilities={},
            ),
        )
        async with stdio_server() as (read_stream, write_stream):
            await srv.run(read_stream, write_stream, init_options)

    asyncio.run(_amain())
```

（上方为 mcp SDK 1.x 标准 stdio 范式；实现时先 `pip show mcp` 确认版本，若所装版本的 `run`/`InitializationOptions` 签名有出入，以 `python -c "import inspect, mcp.server; print(inspect.signature(mcp.server.Server.run))"` 实测为准逐行对齐，禁止臆造 API。）

`__main__.py`：`from fieldwork_agent.mcp_server.server import run_stdio; run_stdio()`。`demo_mcp_client.py`：`mcp.client.stdio` 起本 Server → list_tools 断言 22 → create_project→update_research_context→check_phase_advance 全链路打印 → search_hotels 演示包络（体验模式/无 CLI 时展示 degraded/error，脚本头部注释写明手动运行方式与预期）。**不进 CI**。

- [ ] **Step 4: 确认通过** — `pytest tests/test_mcp_server_contract.py -v`；全量 `pytest -q && ruff check .`
- [ ] **Step 5: 冒烟 + Commit** — 手动 `python demo_mcp_client.py`（本机有 flyai CLI，应见真实结果或体验模式 degraded）；`git add -A mcp_server demo_mcp_client.py tests/test_mcp_server_contract.py pyproject.toml; git commit -m "feat(mcp): server组装+L3同构契约+演示client"`

---

## 验收（整盘）

1. `pytest -q` 全绿（含既有 kernel/runtime 测试无回归）；`ruff check .` 干净；测试不依赖网络/flyai/DashScope key。
2. `fieldwork-mcp` console script 可被标准 MCP 宿主（千问办公/WorkBuddy/Claude 类）以 stdio 接入：22 tools / ≥7 static resources / 2 prompts；`list_tools` 中能力工具 schema 与 `build_tool_schemas()` 全等（同构测试在 CI 钉死）。
3. 无 flyai CLI 环境调用搜索工具返回 `ok:false, code:"flyai_unavailable"` 包络；无 DashScope key 时 compose_proposal 返回模板降级且 `degraded` 可见——全部走包络不走协议异常。
4. demo_mcp_client.py 手动跑通一次并保留输出记录（可作为比赛演示素材）。
