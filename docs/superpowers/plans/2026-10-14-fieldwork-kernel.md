# Fieldwork 能力内核化（Plan 1/5）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 CLI 单体重构出无状态能力内核 `fieldwork_agent/kernel/`（sources/aggregation/documents/guidance/repository/models/llm/registry/loop 九模块），并把 orchestrator+CLI 重接为内核之上的第一个宿主。

**Architecture:** 共享内核、双宿主薄壳（spec §0）；kernel 能力函数严格无状态，持久化经 `ProjectRepository` Protocol 由宿主显式调用；工具决策从关键词路由改为 LLM function calling，schema 由 `registry.build_tool_schemas()` 单一来源生成（后续 MCP/Web 计划共用）。

**Tech Stack:** Python ≥3.10、pydantic v2、stdlib sqlite3（Plan 5 才用）、dashscope SDK、pytest（全 mock、不联网、无 key）。

**Spec:** `docs/superpowers/specs/2026-10-14-fieldwork-agent-mcp-kernel-design.md` §1/§2.1(③④的kernel侧)/§3.1/§3.3(loop核心)/§5.1里程碑1/§6(L1/L2)

**后续计划（本计划不含）:** MCP Server(Plan 2)、Web宿主(Plan 3)、前端(Plan 4)、SQLite(Plan 5)。`tools/flyai_tools.py` 与 `core/state.py` 本计划保留原位并标记 deprecated，由新模块替代后在 Task 10 删除旧决策逻辑。

## Global Constraints

- 测试全部基于 mock：不联网、不调 subprocess、不需要 API Key（延续 DEVELOPMENT.md §2 契约）。
- 依赖方向单向：宿主 → kernel → 外部；kernel 不 import `core/`、`cli`、`mcp`、`fastapi`。
- ruff 规则 `E,F,I,UP,B`，行宽 100；注释中文，标识符英文。
- 模型分档：`FA_INTERVIEW_MODEL`(默认 `qwen3.8-flash`) / `FA_PROPOSAL_MODEL`(默认 `qwen-max`) / `FA_EXTRACT_MODEL`(默认 `qwen3.8-flash`)。
- 统一返回包络：`{ok, data?, error?: {code, message, hint}, degraded?}`；flyai 缺失 → `ok:false, code:"flyai_unavailable"`，不抛异常。
- `search_cache.result` 落盘截断 ≤64KB；进 LLM 消息的工具结果截断 ≤2000 字符。
- Windows：新增输出代码不得在 `cli.py` 的 `_ensure_utf8_stdio()` 之前 print（DEVELOPMENT.md §7）。
- git 命令在本机用绝对路径 `& "C:\Program Files\Git\bin\git.exe"`（PowerShell PATH 无 git）。

## 文件结构

```
fieldwork_agent/
├── kernel/
│   ├── __init__.py        # 导出 CAPABILITIES、build_tool_schemas
│   ├── models.py          # 全部 pydantic 数据契约 + Envelope
│   ├── registry.py        # CapabilityMeta + register() + build_tool_schemas()
│   ├── sources/flyai.py   # 8原语(归一化) ← 迁移 tools/flyai_tools.py 拼装
│   ├── llm.py             # LLMClient(chat支持tools) ← 迁移 tools/dashscope_tools.py
│   ├── guidance/
│   │   ├── phases.py      # 阶段常量/prompts/questions/check_advance
│   │   ├── extract.py     # extract_locations / extract_researcher_info
│   │   └── informants.py  # INFORMANT_LIBRARY + get_informant/get_interview_guide_methodology
│   ├── aggregation/
│   │   ├── site_profile.py  # profile_field_site
│   │   └── itinerary.py     # compose_itinerary
│   ├── documents/proposal.py  # compose_proposal / compose_interview_guide
│   ├── repository/
│   │   ├── protocol.py    # ProjectRepository(Protocol)
│   │   └── json_repo.py   # JsonRepository ← 迁移 core/state.py
│   └── loop.py            # run_tool_loop 同步核心(宿主共用)
├── core/
│   ├── config.py          # Modify: 三槽模型配置
│   └── orchestrator.py    # Rewrite(Task 9): 内核之上的宿主
├── cli/  (现 core/cli.py 暂不动，仅改 phase 展示来源)
tests/
├── conftest.py            # Modify: 增 fake_llm / fake_flyai fixture
├── test_models_registry.py
├── test_sources_flyai.py
├── test_llm_config.py
├── test_guidance.py
├── test_site_profile.py
├── test_itinerary.py
├── test_documents.py
├── repository_contract.py # L2 契约套件(参数化;本计划仅 json 实现)
├── test_json_repo.py
└── test_loop_orchestrator.py
```

---

### Task 1: kernel 骨架 + 数据契约 models.py + registry.py

**Files:**
- Create: `kernel/__init__.py`, `kernel/models.py`, `kernel/registry.py`
- Test: `tests/test_models_registry.py`

**Interfaces:**
- Produces: `Envelope(ok:bool, data:Any=None, error:ToolError|None=None, degraded:str|None=None)`；`ToolError(code:str, message:str, hint:str="")`；`SearchItem(name:str, price:float|None=None, price_raw:str|None=None, jump_url:str|None=None, pic_url:str|None=None, extra:dict={})`；`ItemPage(items:list[SearchItem], total:int, cheapest:float|None, dearest:float|None)`；`FieldSiteProfile(location, transport:ItemPage|None, accommodation:ItemPage|None, culture:list[dict], amenities:ItemPage|None, summary:SummaryHints, generated_at:str)`；`SummaryHints(section_counts:dict[str,int], accommodation_nightly_min:float|None, transport_min:float|None)`；`ItineraryPlan(departure, destinations, start_date:str, end_date:str, legs:list[dict], stays:list[dict], rhythm:list[PhaseSegment], buffer_days:int, alternatives:list[dict], budget:BudgetBreakdown, generated_at)`；`PhaseSegment(label:str, days:int, note:str)`；`BudgetBreakdown(items:list[BudgetItem], total:float, budget_total:float, over_budget:bool)`；`BudgetItem(name:str, amount:float, source:str)`；`ResearchContext(discipline:str="", topic:str="", site_status:str="", scale:str="", budget:str="", time_window:str="", departure_city:str="", target_location:str="", travel_date:str="", extras:dict={})`；`AdvanceResult(can_advance:bool, to_phase:str|None=None, missing_fields:list[str]=[])`；`FieldSiteRecord(name:str, confirmed:bool=False, profile:dict|None=None)`；`InformantRecord(code:str, type:str, relation:str="", contact:str="", notes:str="")`；`ProposalDoc(markdown:str, kind:str="proposal", version:str="draft", sections:list[str]=[])`。`register(meta:CapabilityMeta)`；`build_tool_schemas() -> list[dict]`（OpenAI tools 格式）；`CapabilityMeta(name:str, description:str, fn:Callable, args_model:type[BaseModel], read_only:bool=True, open_world:bool=False)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models_registry.py
from pydantic import BaseModel
from fieldwork_agent.kernel.models import Envelope, ToolError, SearchItem, ResearchContext
from fieldwork_agent.kernel.registry import CapabilityMeta, register, build_tool_schemas

def test_envelope_defaults():
    env = Envelope(ok=False, error=ToolError(code="flyai_unavailable", message="no cli"))
    assert env.ok is False and env.data is None and env.degraded is None

def test_searchitem_price_is_float():
    assert SearchItem(name="X", price=1234.0).price == 1234.0

def test_research_context_defaults_empty():
    ctx = ResearchContext()
    assert ctx.discipline == "" and ctx.extras == {}

def test_build_tool_schemas_openai_format():
    class PingArgs(BaseModel):
        msg: str
        limit: int = 3
    def _ping(msg: str, limit: int = 3) -> Envelope:
        return Envelope(ok=True, data=msg)
    register(CapabilityMeta(name="demo_ping", description="demo tool",
                           fn=_ping, args_model=PingArgs))
    schemas = build_tool_schemas()
    tool = [t for t in schemas if t["function"]["name"] == "demo_ping"][0]
    props = tool["function"]["parameters"]["properties"]
    assert "msg" in props and props["limit"]["default"] == 3
    assert tool["function"]["parameters"].get("required") == ["msg"]
```

- [ ] **Step 2: 运行确认失败** — `pytest tests/test_models_registry.py -v` → ModuleNotFoundError

- [ ] **Step 3: 实现 models.py 与 registry.py**

models.py：按 Produces 清单逐一定义 pydantic `BaseModel`，全字段给默认值（list/dict 用 `Field(default_factory=...)`）。registry.py：

```python
# kernel/registry.py
from dataclasses import dataclass
from typing import Callable
from pydantic import BaseModel

_REGISTRY: dict[str, "CapabilityMeta"] = {}

@dataclass
class CapabilityMeta:
    name: str
    description: str
    fn: Callable
    args_model: type[BaseModel]
    read_only: bool = True
    open_world: bool = False

def register(meta: CapabilityMeta) -> None:
    _REGISTRY[meta.name] = meta

def capabilities() -> dict[str, CapabilityMeta]:
    return dict(_REGISTRY)

def build_tool_schemas() -> list[dict]:
    """单一来源：MCP Server(Plan2) 与 Web 宿主(Plan3) 共用。"""
    out = []
    for m in _REGISTRY.values():
        schema = m.args_model.model_json_schema()
        schema.pop("title", None)
        out.append({"type": "function", "function": {
            "name": m.name, "description": m.description,
            "parameters": schema}})
    return out
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/test_models_registry.py -v` → 4 PASS
- [ ] **Step 5: Commit** — `git add kernel tests/test_models_registry.py; git commit -m "feat(kernel): 数据契约与工具schema单一来源"`

---

### Task 2: sources/flyai.py — 8 原子搜索 + 结果归一化

**Files:**
- Create: `kernel/sources/__init__.py`, `kernel/sources/flyai.py`
- Test: `tests/test_sources_flyai.py`

**Interfaces:**
- Consumes: `Envelope/ItemPage/SearchItem`（Task 1）
- Produces: `parse_price(raw: str | None) -> float | None`；`normalize_items(raw_list: list[dict]) -> list[SearchItem]`；`FlyAIClient(api_key: str = "")` 方法：`search_flights(origin, destination, dep_date="", back_date="", sort_type="2", max_price="") / search_trains(...) / search_hotels(dest_name, check_in="", check_out="", hotel_types="", max_price="", sort="price_asc", poi_name="", hotel_stars="") / search_pois(city_name, keyword="", category="", poi_level="") / search_keyword(query) / search_ai(query) / search_marriott_hotels(...) / search_marriott_packages(keyword, sort_type="price_asc")`，均返回 `Envelope[ItemPage]`；CLI 缺失/超时 → `Envelope(ok=False, error.code ∈ {"flyai_unavailable","flyai_timeout","flyai_error","parse_error"})`。argv 拼装规则与旧 `tools/flyai_tools.py` 完全一致（flyai 命令名/参数名不变）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_sources_flyai.py
import pytest
from fieldwork_agent.kernel.sources.flyai import FlyAIClient, parse_price, normalize_items

@pytest.mark.parametrize("raw,expected", [
    ("¥1,234", 1234.0), ("¥1,234.50", 1234.5), ("1234", 1234.0),
    ("¥999起", 999.0), ("", None), (None, None), ("免费", None)])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected

def test_normalize_items_maps_fields():
    raw = [{"name": "A民宿", "adultPrice": "¥200", "mainPic": "p.jpg",
            "jumpUrl": "j", "unknownField": 1}]
    items = normalize_items(raw)
    assert items[0].name == "A民宿" and items[0].price == 200.0
    assert items[0].pic_url == "p.jpg" and items[0].extra == {"unknownField": 1}

def test_search_hotel_argv_and_envelope(monkeypatch):
    calls = {}
    def fake_run(args):
        calls["args"] = args
        return {"status": 0, "data": {"itemList": [{"title": "B", "price": "¥80"}]}}
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command", fake_run)
    env = client.search_hotels("大理", hotel_types="民宿,客栈", max_price="200")
    assert env.ok and env.data.items[0].name == "B" and env.data.items[0].price == 80.0
    assert env.data.cheapest == 80.0
    assert calls["args"] == ["search-hotel", "--dest-name", "大理",
                             "--hotel-types", "民宿,客栈", "--max-price", "200",
                             "--sort", "price_asc"]

def test_cli_missing_degrades(monkeypatch):
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": -1, "error": "flyai CLI not found"})
    env = client.search_ai("q")
    assert not env.ok and env.error.code == "flyai_unavailable"
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_sources_flyai.py -v`

- [ ] **Step 3: 实现** — `_run_command` 复制旧 `tools/flyai_tools.py:17-31`（subprocess、30s 超时、三种异常分支）；8 方法 argv 拼装从旧文件逐字段迁移（旧参数名 `origin/destination/dep_date...` 保持不变，`search_flight→search_flights` 仅改名）；错误映射：`"not found" in err → flyai_unavailable`，`timed out → flyai_timeout`，其余 `flyai_error`/`parse_error`；`parse_price`：剥 `¥`,`起`,`,`,float 失败返回 None；`normalize_items`：name←`name|title`、price←`parse_price(adultPrice|price)`、pic←`mainPic|picUrl`、jump←`jumpUrl|detailUrl`、剩余键进 `extra`；`ItemPage.total=len(raw_list)`、cheapest/dearest 从解析成功的价格取 min/max。

- [ ] **Step 4: 确认通过** — `pytest tests/test_sources_flyai.py -v` → PASS
- [ ] **Step 5: Commit** — `git commit -am "feat(kernel): flyai原子搜索原语与结果归一化"`

---

### Task 3: llm.py + Config 模型三槽

**Files:**
- Create: `kernel/llm.py`
- Modify: `core/config.py`（替换 chat_model/system_model 段）
- Test: `tests/test_llm_config.py`

**Interfaces:**
- Consumes: Task 1 `Envelope`
- Produces: `LLMClient(api_key: str, models: dict[str, str])`，`models` 键为 `"interview"|"proposal"|"extract"`；`chat(messages: list[dict], *, slot: str = "proposal", tools: list[dict] | None = None, temperature: float = 0.7, max_tokens: int = 4096) -> dict`，返回 `{"content": str, "tool_calls": list[{"id","name","arguments":dict}] | None, "finish_reason": str, "usage": dict}`；无 key 或 SDK 缺失时构造抛 `RuntimeError`（调用方以 `Config.dashscope_available` 预判）。`Config.interview_model / proposal_model / extract_model`（env `FA_INTERVIEW_MODEL`/`FA_PROPOSAL_MODEL`/`FA_EXTRACT_MODEL`，config.json 键同名覆盖）；`Config.llm_models -> dict[str,str]` 属性。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_llm_config.py
def test_config_model_slots(monkeypatch):
    monkeypatch.setenv("FA_INTERVIEW_MODEL", "qwen3.8-flash")
    monkeypatch.setenv("FA_PROPOSAL_MODEL", "qwen-max")
    monkeypatch.delenv("FA_CHAT_MODEL", raising=False)
    monkeypatch.setattr("fieldwork_agent.core.config.CONFIG_DIR", __import__("pathlib").Path("/tmp_nonexist"))
    from fieldwork_agent.core.config import Config
    cfg = Config()
    assert cfg.llm_models == {"interview": "qwen3.8-flash", "proposal": "qwen-max",
                              "extract": "qwen3.8-flash"}

def test_llm_chat_parses_tool_calls(monkeypatch):
    from fieldwork_agent import kernel
    class FakeResp:  # dashscope Generation 返回形状
        status_code = 200
        class output:
            class choices: ...
        usage = {}
    msg = {"content": "", "tool_calls": [{"id": "c1",
           "function": {"name": "search_hotels", "arguments": '{"dest_name":"大理"}'}}]}
    import json as _json
    resp = FakeResp()
    resp.output = type("O", (), {"choices": [type("C", (), {
        "finish_reason": "tool_calls", "message": type("M", (), msg)()})]()})
    monkeypatch.setattr(kernel.llm.Generation, "call", lambda **kw: resp)
    client = kernel.llm.LLMClient(api_key="k", models={"proposal": "m"})
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal", tools=[{}])
    assert out["tool_calls"][0]["name"] == "search_hotels"
    assert out["tool_calls"][0]["arguments"] == {"dest_name": "大理"}
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_llm_config.py -v`
- [ ] **Step 3: 实现** — `LLMClient.chat`：`Generation.call(model=self.models[slot], messages=messages, api_key=..., temperature=..., max_tokens=..., **({"tools": tools} if tools else {}))`；解析 `response.output.choices[0].message`：`tool_calls` 存在则把每项 `function.arguments`（JSON 字符串）`json.loads` 为 dict；`status_code != 200` → 返回 `{"content": "", "tool_calls": None, "error": str(response.message)}`。config.py：删 `chat_model/system_model`，加三槽（默认 `qwen3.8-flash`/`qwen-max`/`qwen3.8-flash`），`save()` 与 `_load_config()` 键同步改名。
- [ ] **Step 4: 确认通过** — `pytest tests/test_llm_config.py -v`；再全量 `pytest -k "not orchestrator"` 排查旧 `chat_model` 引用，把 `orchestrator.py:28` 临时改为 `config.proposal_model`（Task 9 会重写它）。
- [ ] **Step 5: Commit** — `git commit -am "feat(kernel): LLMClient支持function calling并分档模型槽"`

---

### Task 4: guidance — 阶段/提取/访谈对象库

**Files:**
- Create: `kernel/guidance/__init__.py`, `phases.py`, `extract.py`, `informants.py`
- Test: `tests/test_guidance.py`

**Interfaces:**
- Consumes: Task 1 `ResearchContext/AdvanceResult/FieldSiteRecord/InformantRecord`
- Produces: `phases.PHASES: list[str]`（5 阶段，值同旧常量 `research_positioning|field_site_profiles|itinerary_planning|proposal_generation|finished`）；`phases.phase_prompt(phase) -> str`；`phases.guiding_questions(phase) -> list[str]`；`phases.REQUIRED_FIELDS: dict[str, list[str]]`（每阶段需采集的 ResearchContext 字段名）；`phases.check_advance(phase: str, ctx: ResearchContext, sites: list[FieldSiteRecord], informants: list[InformantRecord], has_itinerary: bool, has_proposal: bool) -> AdvanceResult`；`extract.extract_locations(message: str, existing: dict) -> dict[str, str]`；`extract.extract_researcher_info(message: str) -> dict`（键映射到 ResearchContext 字段：`discipline/topic/scale/budget/time_window/target_location/extras.region`）；`informants.INFORMANT_LIBRARY: dict[str, dict]`（≥10 类，每类 `{name, approach, cautions, question_domains: list[str], ethics}`）；`informants.get_informant(type: str) -> dict | None`；`informants.INTERVIEW_GUIDE_METHODOLOGY: str`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_guidance.py
from fieldwork_agent.kernel.guidance import phases, extract, informants
from fieldwork_agent.kernel.models import ResearchContext, FieldSiteRecord, InformantRecord

def test_check_advance_a_to_b_needs_4_fields():
    ctx = ResearchContext(discipline="人类学", topic="宗族", scale="单人", budget="2万")
    r = phases.check_advance("research_positioning", ctx, [], [], False, False)
    assert r.can_advance and r.to_phase == "field_site_profiles"
    r2 = phases.check_advance("research_positioning",
        ResearchContext(discipline="人类学", topic="x"), [], [], False, False)
    assert not r2.can_advance and set(r2.missing_fields) & {"scale", "budget"}

def test_check_advance_b_to_c_requires_site_and_informant():
    site = FieldSiteRecord(name="喜洲", confirmed=True)
    ctx = ResearchContext(target_location="喜洲")
    no_informant = phases.check_advance("field_site_profiles", ctx, [site], [], False, False)
    assert not no_informant.can_advance, "B→C 缺访谈对象必须不推进(spec §2.1)"
    ok = phases.check_advance("field_site_profiles", ctx, [site],
                              [InformantRecord(code="P1", type="长老")], False, False)
    assert ok.can_advance and ok.to_phase == "itinerary_planning"

def test_extract_locations_from_message():
    loc = extract.extract_locations("我从北京出发去大理调研白族村落", {})
    assert loc == {"target_location": "大理", "departure_city": "北京"}

def test_informant_library_shape():
    assert len(informants.INFORMANT_LIBRARY) >= 10
    one = informants.get_informant("长老") or next(iter(informants.INFORMANT_LIBRARY.values()))
    assert one["approach"] and one["question_domains"] and one["ethics"]

def test_prompts_have_no_placeholders():
    for p in phases.PHASES:
        assert "{" not in phases.phase_prompt(p).replace("{{", "").replace("}}", "")
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_guidance.py -v`
- [ ] **Step 3: 实现** — `phase_prompt`/`guiding_questions` 从 `core/dialogue_engine.py` L14-106 的 `SYSTEM_PROMPTS`/`GUIDING_QUESTIONS` 原文迁移（键改字符串常量）；`check_advance`：A→B 条件 `sum(1 for f in REQUIRED_FIELDS["research_positioning"] if getattr(ctx, f, None) or ctx.extras.get(f)) >= 4`；B→C 条件 `any(s.confirmed for s in sites) and len(informants) >= 1`；C→D 条件 `has_itinerary`；D→finished 条件 `has_proposal`；`extract.py` 迁移 `_extract_locations_from_message`(L402-486) 与 `extract_researcher_info_from_message`(L503-537)，键名按 Interfaces 映射；`informants.py` 编写 10+ 类：长老/宗族头人/宗教人士/村干部/驻村书记/乡村教师/妇女/返乡务工者/集市商户/非遗传承人/乡医，各含四字段。
- [ ] **Step 4: 确认通过** — `pytest tests/test_guidance.py -v`
- [ ] **Step 5: Commit** — `git commit -am "feat(kernel): 引导策略与访谈对象库纯数据化"`

---

### Task 5: aggregation/site_profile.py — profile_field_site

**Files:**
- Create: `kernel/aggregation/__init__.py`, `kernel/aggregation/site_profile.py`
- Test: `tests/test_site_profile.py`

**Interfaces:**
- Consumes: `FlyAIClient`（Task 2）、`FieldSiteProfile/ItemPage/SummaryHints/Envelope`（Task 1）、registry `register/CapabilityMeta`（Task 1）
- Produces: `profile_field_site(location: str, budget_per_night: float = 200, interests: list[str] | None = None, departure_city: str = "", travel_date: str = "", max_items_per_section: int = 5, *, client: FlyAIClient | None = None) -> Envelope`（data=`FieldSiteProfile`）；模块导入时 `register()` 能力名 `"profile_field_site"`。默认 interests=`["人文古迹","宗教场所","古镇古村","博物馆"]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_site_profile.py
from fieldwork_agent.kernel.aggregation.site_profile import profile_field_site
from fieldwork_agent.kernel.models import ItemPage, SearchItem, Envelope

class StubClient:
    def __init__(self): self.calls = []
    def _page(self, items): return Envelope(ok=True, data=ItemPage(items=items, total=len(items),
                                            cheapest=None, dearest=None))
    def search_hotels(self, dest_name, **kw):
        self.calls.append(("hotel", kw.get("hotel_types", "")))
        return self._page([SearchItem(name="M", price=180.0)]) if "民宿" in kw.get("hotel_types","") else Envelope(ok=False)
    def search_pois(self, city_name, **kw):
        self.calls.append(("poi", kw.get("category","")))
        return self._page([SearchItem(name="祠堂")])
    def search_ai(self, query): self.calls.append(("ai", query)); return Envelope(ok=False)
    def search_keyword(self, query): return self._page([SearchItem(name="节庆活动")])
    def search_flights(self, **kw): return self._page([SearchItem(name="MU5843", price=620.0)])
    def search_trains(self, **kw): return Envelope(ok=False)

def test_profile_sections_and_failure_isolation():
    c = StubClient()
    env = profile_field_site("大理", client=c, departure_city="昆明", travel_date="2026-11-01")
    p = env.data
    assert p.accommodation.items[0].name == "M"          # 民宿段成功
    assert p.transport is not None                        # 航班成功、火车失败但段不缺
    assert p.culture and p.amenities
    # budget筛选：民宿价180 ≤ 200 保留
def test_budget_filter_drops_over():
    class Over(StubClient):
        def search_hotels(self, dest_name, **kw):
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name="贵宿", price=350.0)], total=1))
    env = profile_field_site("大理", budget_per_night=200, client=Over())
    assert len(env.data.accommodation.items) == 0  # 350 超上浮线(200*1.5=300)被剔除
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_site_profile.py -v`
- [ ] **Step 3: 实现** — 用 `concurrent.futures.ThreadPoolExecutor(max_workers=6)` 并行调用：民宿+酒店两次 `search_hotels`、每 interest 一次 `search_pois`、`search_keyword(f"{location} 文化活动 节庆 民俗")`、可选 departure_city 时 `search_flights/search_trains`、一次 `search_ai`；逐原语 try/except 失败隔离（单段失败置 None/[] 不影响整体 `ok=True`，全部失败才 `ok=False, code="all_sources_failed"`）；住宿筛选沿用旧策略：民宿上限 `budget_per_night * 1.5`；每段截断 `max_items_per_section`；`SummaryHints` 从成功段统计；`generated_at=datetime.now().isoformat()`。
- [ ] **Step 4: 确认通过** — `pytest tests/test_site_profile.py -v`
- [ ] **Step 5: Commit** — `git commit -am "feat(kernel): profile_field_site聚合能力(失败隔离+预算筛选)"`

---

### Task 6: aggregation/itinerary.py — compose_itinerary（含日期修复）

**Files:**
- Create: `kernel/aggregation/itinerary.py`
- Test: `tests/test_itinerary.py`

**Interfaces:**
- Consumes: `FlyAIClient`、`ItineraryPlan/PhaseSegment/BudgetBreakdown/BudgetItem`（Task 1）、`profile_field_site`（Task 5，跨点画像复用）
- Produces: `compose_itinerary(departure: str, destinations: list[str], start_date: str, duration_days: int, budget_total: float, researcher_count: int = 1, *, client: FlyAIClient | None = None) -> Envelope`（data=`ItineraryPlan`）；注册能力 `"compose_itinerary"`。日期一律 `datetime.strptime(start_date, "%Y-%m-%d") + timedelta`，**禁止字符串拼接日期**。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_itinerary.py
from fieldwork_agent.kernel.aggregation.itinerary import compose_itinerary

class StubClient:  # 复用 Task5 形状，此处全返回低价成功
    def search_flights(self, **kw): return _ok([("MU", 600.0)])
    def search_trains(self, **kw): return _ok([("G29", 350.0)])
    def search_hotels(self, dest_name, **kw): return _ok([("民宿", 150.0)])
    def search_pois(self, city_name, **kw): return _ok([("祠堂", None)])
    def search_keyword(self, query): return _ok([("民俗", None)])
    def search_ai(self, query): return _ok([])

def _ok(pairs):
    from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem
    return Envelope(ok=True, data=ItemPage(
        items=[SearchItem(name=n, price=p) for n, p in pairs], total=len(pairs)))

def test_dates_across_month_boundary():
    env = compose_itinerary("昆明", ["大理"], "2026-11-28", 6, 20000, client=StubClient())
    plan = env.data
    assert plan.end_date == "2026-12-03"          # 旧实现此处会拼出非法日期
    assert plan.stays[0]["check_out"] == "2026-12-03"

def test_rhythm_and_budget():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 30, 20000, client=StubClient())
    p = env.data
    labels = [s.label for s in p.rhythm]
    assert labels == ["适应期", "深入期", "补充期"] and sum(s.days for s in p.rhythm) <= 30
    assert p.buffer_days >= 1
    assert p.budget.items and abs(p.budget.total - sum(i.amount for i in p.budget.items)) < 0.01
    assert p.budget.over_budget is (p.budget.total > 20000)

def test_legs_pick_cheapest_transport():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000, client=StubClient())
    leg = env.data.legs[0]
    assert leg["chosen"]["price"] == 350.0 and leg["alternatives"]  # 火车600→选350, 保留备选
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_itinerary.py -v`
- [ ] **Step 3: 实现** — 每目的地：去回程交通对比（flight/train items 合并取最低价入 `legs[].chosen`，其余入 `alternatives`）；住宿按 `duration_days` 均分点到各 dest（`timedelta` 滚动 check_in/check_out 入 `stays`）；rhythm：30% 适应 / 50% 深入 / 20% 补充（天数为 0 的段并入前段）；`buffer_days = max(1, duration_days // 10)`；budget items：交通 `chosen.price * researcher_count * 2`、住宿 `price/晚 * 天数`、`BudgetItem(name="市内与餐饮", amount=0.15*budget_total, source="estimate")`；total>budget_total 置 over_budget。
- [ ] **Step 4: 确认通过** — `pytest tests/test_itinerary.py -v`
- [ ] **Step 5: Commit** — `git commit -am "feat(kernel): compose_itinerary节奏缓冲与跨年日期修复"`

---

### Task 7: documents/proposal.py — compose_proposal + compose_interview_guide

**Files:**
- Create: `kernel/documents/__init__.py`, `kernel/documents/proposal.py`
- Test: `tests/test_documents.py`

**Interfaces:**
- Consumes: `LLMClient`（Task 3）、`phases.phase_prompt`（Task 4）、`informants`（Task 4）、全部 Task 1 models
- Produces: `compose_proposal(ctx: ResearchContext, sites: list[dict], itinerary: dict | None, informants: list[dict], knowledge: list[dict], *, llm: LLMClient | None = None, section: str | None = None, prior_markdown: str = "") -> ProposalDoc`；`compose_interview_guide(topic: str, informant_types: list[str], *, llm: LLMClient | None = None) -> dict[str, str]`；注册能力 `"compose_proposal"`、`"compose_interview_guide"`。`section` 非空时仅重写该章（配合 `prior_markdown`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_documents.py
import json
from fieldwork_agent.kernel.documents.proposal import compose_proposal, compose_interview_guide, build_context_block
from fieldwork_agent.kernel.models import ResearchContext

CTX = ResearchContext(discipline="社会学", topic="宗族变迁", scale="单人调研",
                      budget="2万", time_window="6-9月", target_location="喜洲")
SITES = [{"name": "喜洲", "confirmed": True, "profile": {"accommodation": "..."}}]

class FakeLLM:
    def __init__(self, out): self.out, self.messages = out, []
    def chat(self, messages, **kw):
        self.messages = messages
        return {"content": self.out, "tool_calls": None, "finish_reason": "stop", "usage": {}}

def test_context_block_contains_all_channels():
    block = build_context_block(CTX, SITES, {"legs": []}, [{"code": "P1", "type": "长老"}],
                                [{"title": "旧笔记"}])
    for token in ["宗族变迁", "喜洲", "legs", "长老", "旧笔记"]:
        assert token in block

def test_channel_absent_asserts():
    import pytest
    with pytest.raises(ValueError, match="缺少渠道"):
        build_context_block(CTX, [], {"legs": []}, [{"code": "P1"}], [])  # sites为空

def test_degrade_without_llm():
    doc = compose_proposal(CTX, SITES, {"legs": []}, [{"code": "P1"}], [], llm=None)
    assert doc.markdown.startswith("# ") and "## 四、详细行程安排" in doc.markdown
    assert len(doc.sections) == 8

def test_interview_guide_per_type():
    llm = FakeLLM("提纲正文")
    guides = compose_interview_guide("宗族变迁", ["长老", "集市商户"], llm=llm)
    assert set(guides) == {"长老", "集市商户"}
    assert "知情同意" in llm.messages[-1]["content"]
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_documents.py -v`
- [ ] **Step 3: 实现** — `build_context_block`：五个渠道（研究者信息/田野点/行程/访谈对象/知识库）各渲染为 markdown 小节，任一渠道为空 raise `ValueError("缺少渠道: <名>")`（spec L1 断言要求）；`compose_proposal`：有 llm 时 `llm.chat([{"role":"system","content":phase_prompt("proposal_generation")},{"role":"user","content":f"{context_block}\n\n请生成完整研究方案"}], slot="proposal", max_tokens=8192, temperature=0.5)`，`sections` 用正则 `^## .+` 提取；无 llm 时模板拼接（八章标题 + 渠道数据直嵌 + "（模板降级版）"尾注）；guide 的 user prompt 固定包含 `informants.INTERVIEW_GUIDE_METHODOLOGY` 与"知情同意话术"字样。
- [ ] **Step 4: 确认通过** — `pytest tests/test_documents.py -v`
- [ ] **Step 5: Commit** — `git commit -am "feat(kernel): 提案与访谈提纲文档生成(含无key模板降级)"`

---

### Task 8: repository Protocol + JsonRepository + L2 契约套件

**Files:**
- Create: `kernel/repository/__init__.py`, `kernel/repository/protocol.py`, `kernel/repository/json_repo.py`
- Test: `tests/repository_contract.py`, `tests/test_json_repo.py`

**Interfaces:**
- Consumes: `ProjectState` 现有逻辑（迁移源）、Task 1 models
- Produces: `ProjectRepository`（`typing.Protocol`），方法：`create(project_id) -> None`；`list_projects() -> list[dict]`；`get_state(pid) -> dict`；`get_phase(pid) -> str`；`set_phase(pid, phase) -> None`；`update_research_context(pid, patch: dict) -> None`；`get_research_context(pid) -> dict`；`append_message(pid, role, content, meta=None) -> None`；`get_messages(pid, limit=30) -> list[dict]`；`save_field_site(pid, record: dict) -> str`；`list_field_sites(pid) -> list[dict]`；`save_informant(pid, record: dict) -> str`；`list_informants(pid) -> list[dict]`；`cache_search(pid, query, tool, result: dict) -> str`（内部截断≤64KB）；`list_cached_searches(pid) -> list[dict]`；`get_cached_search(cid) -> dict | None`；`add_knowledge(pid, title, content, source="user", tags=None, category="general") -> str`；`list_knowledge(pid) -> list[dict]`；`search_knowledge(pid, text) -> list[dict]`；`delete_knowledge(pid, entry_id) -> None`；`save_proposal(pid, markdown, kind="proposal", version="draft") -> str`；`get_latest_proposal(pid) -> dict | None`；`list_proposals(pid) -> list[dict]`。`JsonRepository(projects_dir: Path)` 实现全部方法（目录布局沿用 `state.json + knowledge/ + search_cache/ + output/`，chat 全量进 `chat_history.jsonl`、state 内滚动窗口 50 条）。契约套件以 `REPO_FACTORIES = [make_json_repo]` 参数化（Plan 5 追加 sqlite 项，套件零改动）。

- [ ] **Step 1: 写契约套件（先只对 json 跑红/跑绿循环）**

```python
# tests/repository_contract.py
import pytest
from fieldwork_agent.kernel.repository.json_repo import JsonRepository

def make_json_repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")

REPO_FACTORIES = [make_json_repo]

@pytest.fixture(params=REPO_FACTORIES)
def repo(request, tmp_path):
    r = request.param(tmp_path)
    r.create("p1")
    return r

class TestContract:
    def test_phase_roundtrip(self, repo):
        assert repo.get_phase("p1") == "research_positioning"
        repo.set_phase("p1", "field_site_profiles")
        assert repo.get_state("p1")["phase_history"][-1]["to"] == "field_site_profiles"

    def test_unknown_phase_rejected(self, repo):
        with pytest.raises(ValueError):
            repo.set_phase("p1", "no_such_phase")

    def test_messages_full_history(self, repo):
        for i in range(60):
            repo.append_message("p1", "user", f"m{i}")
        assert len(repo.get_messages("p1", limit=100)) == 60   # 无丢失（旧双轨制的坑）

    def test_cache_truncated_64kb(self, repo):
        big = {"blob": "x" * 100_000}
        cid = repo.cache_search("p1", "q", "search_ai", big)
        stored = json.loads((repo.root / "p1" / "search_cache" / f"{cid}.json").read_text(encoding="utf-8"))
        assert len(stored["result"]["blob"]) <= 64 * 1024

    def test_proposal_versions(self, repo):
        repo.save_proposal("p1", "v1", version="draft")
        repo.save_proposal("p1", "v2", version="draft")
        assert len(repo.list_proposals("p1")) == 2
        assert repo.get_latest_proposal("p1")["content"] == "v2"

    def test_informants_and_sites(self, repo):
        repo.save_field_site("p1", {"name": "喜洲", "confirmed": True})
        repo.save_informant("p1", {"code": "P1", "type": "长老"})
        assert repo.list_field_sites("p1")[0]["confirmed"] is True
        assert repo.list_informants("p1")[0]["type"] == "长老"
```

- [ ] **Step 2: 确认失败** — `pytest tests/repository_contract.py -v`
- [ ] **Step 3: 实现** — `protocol.py` 按 Interfaces 全签名 `... -> ...` Protocol 声明；`json_repo.py` 迁移 `core/state.py` 逻辑：`append_message` 改"全量写 jsonl + state 滚动窗口"单一路径（读时只读 jsonl，去掉现有 `get_recent_messages` 的双轨合并复杂度）；`cache_search` 序列化后 >64KB 则置 `result={"truncated": True, "preview": s[:64*1024]}`；`save_proposal` 落 `output/{kind}_{version}_{ts}.md` 并 state 记 `proposals[]`；`search_knowledge` 关键词过滤 title+content+tags（FTS 属 Plan 5）。
- [ ] **Step 4: 确认通过** — `pytest tests/repository_contract.py tests/test_json_repo.py -v`
- [ ] **Step 5: 迁移存量断言测试** — 在 `test_json_repo.py` 增加：读一份 fixture 旧版项目目录（`tests/fixtures/legacy_project/`，手工从 `_default_state` 构造样例），`JsonRepository` 打开后 `get_state` 各键完整、旧 `proposal_draft.filename` 可被 `list_proposals` 兼容读出。跑绿后提交：`git commit -am "feat(kernel): ProjectRepository契约与Json实现(单轨消息史)"`

---

### Task 9: loop.py 共享循环核心 + orchestrator 重接 + CLI 适配

**Files:**
- Create: `kernel/loop.py`
- Modify: `core/orchestrator.py`（重写）、`core/cli.py`（phase 显示与命令接线）、`tests/conftest.py`
- Test: `tests/test_loop_orchestrator.py`

**Interfaces:**
- Consumes: `build_tool_schemas/capabilities()`（Task 1）、`LLMClient`（Task 3）、`profile_field_site/compose_itinerary`（Task 5/6）、`compose_proposal`（Task 7）、`JsonRepository`（Task 8）、`guidance`（Task 4）、`FlyAIClient`（Task 2）
- Produces: `run_tool_loop(llm: LLMClient, messages: list[dict], dispatch: Callable[[str, dict], Envelope], tools: list[dict], *, max_iters: int = 8, slot: str = "interview", on_event: Callable[[str, dict], None] | None = None) -> LoopResult(reply: str, tool_events: list[dict], iterations: int, stopped_by_limit: bool)`；`dispatch(name, args) -> Envelope`；`FieldAgent`（orchestrator）新签名：`__init__(config)` 组装 `JsonRepository/FlyAIClient/LLMClient(可选)/guidance`，`chat(msg, use_tools=True) -> dict` 返回键 `{reply, phase, tool_events, phase_transition}`。

- [ ] **Step 1: 写 loop 核心失败测试**

```python
# tests/test_loop_orchestrator.py
from fieldwork_agent.kernel.loop import run_tool_loop
from fieldwork_agent.kernel.models import Envelope

class ScriptedLLM:
    def __init__(self, script): self.script = list(script)
    def chat(self, messages, **kw): return self.script.pop(0)

def test_two_tool_rounds_then_answer():
    calls = []
    def dispatch(name, args):
        calls.append(name)
        return Envelope(ok=True, data={"items": []})
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [{"id": "1", "name": "search_hotels",
                                       "arguments": {"dest_name": "大理"}}]},
        {"content": "", "tool_calls": [{"id": "2", "name": "search_pois",
                                       "arguments": {"city_name": "大理"}}]},
        {"content": "回答完毕", "tool_calls": None, "finish_reason": "stop"}])
    r = run_tool_loop(llm, [{"role": "user", "content": "hi"}], dispatch, tools=[])
    assert r.reply == "回答完毕" and calls == ["search_hotels", "search_pois"]
    assert r.iterations == 3 and not r.stopped_by_limit

def test_iteration_cap():
    tc = {"content": "", "tool_calls": [{"id": "x", "name": "search_ai",
                                         "arguments": {"query": "q"}}], "finish_reason": "tool_calls"}
    llm = ScriptedLLM([tc] * 10)
    def dispatch(name, args): return Envelope(ok=True, data={})
    r = run_tool_loop(llm, [{"role": "user", "content": "hi"}], dispatch, tools=[], max_iters=8)
    assert r.stopped_by_limit, "超过8迭代必须停止(spec §3.3)"

def test_tool_error_envelope_goes_back_as_tool_message():
    def dispatch(name, args):
        return Envelope(ok=False, error=type("E", (), {"code": "flyai_unavailable",
                        "message": "no cli", "model_dump": lambda: {"code": "flyai_unavailable",
                        "message": "no cli", "hint": ""}})())
    seen = {}
    def on_event(ev, payload): seen[ev] = payload
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [{"id": "1", "name": "search_ai",
                                       "arguments": {}}], "finish_reason": "tool_calls"},
        {"content": "CLI不可用", "tool_calls": None}])
    r = run_tool_loop(llm, [{"role": "user", "content": "x"}], dispatch, tools=[],
                      on_event=on_event)
    assert r.reply == "CLI不可用" and on_event 收到 tool_started + tool_completed(ok=False)
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_loop_orchestrator.py -v`
- [ ] **Step 3: 实现 loop.py（~60 行）** — while 迭代：<br>①`resp = llm.chat(messages, tools=tools, slot=slot)`；②无 `tool_calls` → return `LoopResult(resp["content"], ...)`；③每条 call：`on_event("tool_started", {...})` → `dispatch(call.name, args)`（args 非 dict 时 `json.loads` 容错）→ 结果 `json.dumps(env.model_dump(mode="json"), ensure_ascii=False)` **截 2000 字符** append 为 `{"role": "tool", "tool_call_id": id, "content": s}` → `on_event("tool_completed", {"call_id": id, "ok": env.ok, "data": env.data, "degraded": env.degraded})`；④超 `max_iters` → `stopped_by_limit=True`，追加 system 提示"迭代已达上限，请基于已有信息回答"再取一次最终回答后返回。未知工具名 → 直接 Envelope(ok=False, code="unknown_tool")。
- [ ] **Step 4: orchestrator 重写** — `FieldAgent.__init__`：`self.repo=JsonRepository(config.projects_dir)`、`self.flyai=FlyAIClient(config.flyai_api_key)`、`self.llm=LLMClient(...) if config.dashscope_available else None`、`self.client_kernel_dispatch(name, args)` 覆盖 registry 全部能力（搜索类透传 flyai、聚合/文档类注入 client/llm）。`chat(message)`：extract 提取并 `repo.update_research_context` → 组装 system=`phase_prompt + 工具指引 + repo快照摘要` → `run_tool_loop` → 轮末：搜索/画像/行程事件 → `repo.cache_search`；`profile/compose_itinerary` 成功 data → `repo.save_field_site/（itinerary 存 state）` → `check_advance(...)` 确定性判定，可推进则 `repo.set_phase`；返回 `{reply, phase, tool_events, phase_transition}`。**删除** `DialogueEngine` 依赖与 `_rule_based_reply` 保留（无 LLM 时仍走旧规则文案）。`cli.py`：`phase_names` 改从 `kernel.guidance.phases` 导入；`generate` 命令改调 `compose_proposal(...)` + `repo.save_proposal`。
- [ ] **Step 5: 接线测试** — `pytest tests/test_loop_orchestrator.py -k orchestrator` 增加一条：FakeLLM 脚本驱动 `FieldAgent.chat("我想去大理调研白族，单人三个月预算2万")`，断言 repo 中 `research_context.target_location == "大理"`、出现 `phase_transition` 或 missing_fields 合理。全量 `pytest -v` + `ruff check .` 通过后：`git commit -am "refactor!: orchestrator重接为内核宿主,关键词路由废弃,共享run_tool_loop"`

---

### Task 10: 删除旧决策核心 + 文档更新

**Files:**
- Delete: `core/dialogue_engine.py`、`tools/flyai_tools.py`、`tools/dashscope_tools.py`、`core/state.py`、`tests/test_flyai_tools.py`
- Modify: `core/__init__.py`、`tools/__init__.py`、`__main__.py`、`DEVELOPMENT.md`、`pyproject.toml`（如含旧模块引用）

**Interfaces:**
- Consumes: Task 9 完成后旧模块零引用（删除前先验证）
- Produces: 最终包结构 = Global 文件结构中 kernel + core(config, orchestrator, cli)；DEVELOPMENT.md §4 架构图与 §5 扩展点改写为内核宿主版。

- [ ] **Step 1: 引用清查** — `Grep core.dialogue_engine|from fieldwork_agent.core import|DialogueEngine|ProjectState|FlyAITools|DashScopeChat` 全仓库；残留引用逐一修掉（预期仅测试注释级别）。
- [ ] **Step 2: 删除文件** — 上列 5 个文件删除；`tests/` 中 import 旧模块的用例删除或改用 kernel。
- [ ] **Step 3: 全量回归** — `pytest -v && ruff check .` 必须全绿；启动冒烟：真实终端 `python -m fieldwork_agent` 能进入 `new/list/help`（不要求 LLM 对话）。
- [ ] **Step 4: 更新 DEVELOPMENT.md** — §4 架构图替换为本计划文件结构；§5.1 扩展点改为"kernel/sources 加方法 + registry.register + 契约测试"；§5.2 关键词路由条目删除，注明"工具决策已移交宿主 LLM function calling"；§9 Roadmap 勾掉"orchestrator 流式测试"以外本计划完成项。
- [ ] **Step 5: Commit** — `git commit -am "chore!: 删除旧单体对话核心,内核成为唯一实现"`

---

## 验收（整盘）

1. `pytest -v` 全绿且不含任何联网/子进程/真实 key 调用；`ruff check .` 干净。
2. `python -m fieldwork_agent` 冒烟：新建项目→输入研究描述→`status` 显示采集字段与阶段→`generate` 无 key 时产出八章模板提案、有 key 时产出 LLM 提案。
3. `from fieldwork_agent.kernel import build_tool_schemas; build_tool_schemas()` 输出 5 个已注册能力（`profile_field_site/compose_itinerary/compose_proposal/compose_interview_guide` + demo），作为 Plan 2/3 的接入面。
