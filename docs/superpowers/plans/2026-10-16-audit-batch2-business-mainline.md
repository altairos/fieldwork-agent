# 审计第二批修复：业务主线闭环（F03/F04/F09/F12/F13）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地《2026-10-16 项目开发现状审计》第二批五项修复，完成一条真实业务主线：F03 Web 对话把研究信息写回结构化状态（确定性规则抽取打底 + 受限状态写工具）、F04 文档生成后落盘保存并提供方案确认入口（确认级失效标记）、F09 消除当前用户消息重复发送给模型、F12 非访谈研究的阶段规则与文档规则一致（NA 豁免）、F13 文档版本/类型切换支持按 ID 读取正文与下载。退出条件（审计原文）：通过真实 FastAPI 的用户入口从空项目走到 finished；刷新后状态和文档仍在；用户修改日期/预算后旧产物标记失效；先用确定性 stub 跑通，再进行一次受控真实 API 验收。

**Architecture:** 抽取逻辑从 `core/orchestrator.py::_apply_extract` 下沉到 `kernel/guidance/extract.py` 成为 `apply_message_extractions`（新增 `extract_time_window`），CLI 与 Web 回合轮末共用（F03 规则打底）；`web_host/agent/state_tools.py` 参照 `mcp_server/state_tools.py` 的宿主层注入模式提供 `update_research_context` 受限写工具（白名单 12 字段），并入回合工具列表（F03 对话内修正）；`loop.py::_persist_tool_results` 补 `compose_proposal`/`compose_interview_guide` 落盘分支（F04 前半）；`JsonRepository` 新增 `confirm_field_site`/`confirm_proposal`/`get_proposal_by_id`/`is_proposal_confirmation_stale`（复用 `kernel.models` 的 `ConfirmationRecord`/`compute_inputs_hash`/`inputs_stale`），`web_host/api/state.py` 暴露 `PUT /research-context`、田野点确认、候选采纳、方案确认端点，确认后复用 `loop.check_and_advance` 推进阶段，stale 确认不再支撑 finished（F04 后半 + 失效标记）；F09 为 `loop.py` 两行交换（先构建消息再落盘用户消息）；F12 在 `proposal.py` 抽取 `_informants_bullets` 共享 helper，`interviews_not_applicable` 时豁免"缺少渠道"校验；F13 在 `exports.py` 新增按 `doc_id` 读取/下载端点，前端 `ProposalView` 版本/kind 切换按需拉正文；前端右栏三面板（田野点确认/候选采纳/研究信息编辑 + NA 开关）走 REST 显式入口。

**Tech Stack:** Python 3.10+ / FastAPI / pydantic（含 `model_dump(mode="json")`）/ pytest（httpx.ASGITransport、asyncio_mode=auto）；前端 React 18 + TypeScript + Vite + vitest（jsdom）。

**Spec:** `docs/reviews/2026-10-16-项目开发现状审计.md` 第二批清单（"采集与修正字段、确认田野点、采纳对象、行程保存、文档持久化、方案确认、按版本读取/下载，并支持非访谈研究"）及第二批验收标准。

## Global Constraints

- **kernel 分层**：`kernel/` 仅依赖标准库与 pydantic，禁止 import `core`/`cli`/`mcp_server`/`web_host`（`json_repo.py` 新增 import `kernel.models` 合法）。
- **绝对导入**：包内跨模块导入一律 `from fieldwork_agent.<pkg>.<mod> import ...`。
- **Web 测试模式**：httpx `ASGITransport` 不触发 lifespan，app fixture 必须手动调 `init_deps(app, config=None, repo=repo)`（照抄 `tests/test_web_projects_api.py:7-23`）。
- **Web 回合测试模式**：`FakeLLM.chat_stream` 脚本化 + `self.calls` 记录（照抄 `tests/test_web_agent_loop.py:10-18`）；`asyncio.run(run_turn(...))` 直调。
- **异步测试无装饰器**：pytest 配置 `asyncio_mode=auto`，`async def test_x(client):` 直接写、不加 `@pytest.mark.asyncio`。
- **错误折叠**：kernel 工具错误折 `Envelope(ok=False, error=ToolError(...))`；Web 折 `fastapi.HTTPException`（400 参数错 / 404 不存在），错误消息不回显用户原始输入。
- **repo dict 进出契约**：新增方法遵循 Protocol 约定（未知 pid → KeyError；返回副本不改磁盘），`protocol.py` 文档字符串同步补方法说明，`tests/repository_contract.py` 追加契约用例（类内 `def test_x(self, repo)` 模式，项目 id 硬编码 `"p1"`）。
- **`_load` 的 setdefault 补齐**：`_default_state` 新增键对存量项目自动补默认值（`_load` 已实现），无需离线迁移；但代码中新方法读键一律 `state.get(...) or ...` 防御。
- **提交纪律**：每任务一次提交，中文消息，`feat:`/`fix:`/`refactor:`/`test:`/`chore:` 前缀；pwsh 传中文消息用 `[IO.File]::WriteAllText($path, $msg, [UTF8Encoding]::new($false))` + `git commit -F`（防乱码）；`git add` 针对性列出文件（工作树常有用户遗留：`M .gitignore`、`?? .superpowers/`、`?? .tmp_home/`，不得带入）。
- **命令工作目录**：Python 命令在仓库根 `f:\Agents_dev\Fieldwork_Agent`（PowerShell）执行，解释器一律 `.venv\Scripts\python.exe`（全局 pytest 插件链坏）；全量 pytest **不显式加 `-q`**（addopts 已有 `-q`，叠加会吞计数行）；前端命令先 `cd web`；跑 npm 包/长输出前先 `[Console]::OutputEncoding = [Text.Encoding]::UTF8`。
- **前端刷新模式**：写操作成功后 `rest.getProject(projectId).then(snap => useProjectStore.getState().loadSnapshot(snap))`（`stores/project.ts` 的 `loadSnapshot` 已有）。

## 任务总览（11 任务，每任务一提交）

| # | 主题 | 审计项 | 核心文件 |
|---|------|--------|----------|
| 1 | 抽取下沉 kernel（time_window + 组合器） | F03 基石 | kernel/guidance/extract.py |
| 2 | proposal NA 豁免 | F12 | kernel/documents/proposal.py |
| 3 | repo 确认基础设施 + 按 ID 读文档 | F04/F13 数据层 | kernel/repository/json_repo.py |
| 4 | Web 回合循环修复（去重/抽取/落盘） | F09/F03/F04 | web_host/agent/loop.py |
| 5 | Web 状态写工具 | F03 | web_host/agent/state_tools.py（新建） |
| 6 | 状态 API + 候选转记录 + stale 判定接线 | F04/F12 | web_host/api/state.py（新建） |
| 7 | 文档按 ID 读取/下载 + created_at 口径 | F13 | web_host/api/exports.py |
| 8 | 前端 API/类型层 | 全部 | web/src/api/rest.ts、types/models.ts |
| 9 | 前端右栏确认/采纳/编辑 | F03/F04 | web/src/components/right/* |
| 10 | 前端 ProposalView 正文拉取 + 方案确认 | F04/F13 | web/src/routes/ProposalView.tsx |
| 11 | 收尾：lint + 全量回归 + stub 主线集成 | 退出条件 | tests/test_web_mainline_e2e.py（新建） |

---

### Task 1: 抽取下沉 kernel——extract_time_window + apply_message_extractions

**Files:**
- Modify: `kernel/guidance/extract.py`（文件末尾追加；模块 docstring L1-30 与点号键约定 L11-12 保持不变）
- Modify: `core/orchestrator.py:34`（import 行）、`core/orchestrator.py:259-280`（`_apply_extract` 换用组合器）
- Test: `tests/test_guidance.py`（文件末尾追加）

**Interfaces:**
- Consumes: `extract_locations(message, existing) -> dict`、`extract_researcher_info(message) -> dict`（本文件既有，签名不变）。
- Produces（Task 4 消费，签名精确）:
  - `extract_time_window(message: str) -> dict[str, str]` —— 按优先级匹配年月区间/年月/月份区间/月份/假期词，命中返回 `{"time_window": m.group(0)}`，否则 `{}`。
  - `apply_message_extractions(message: str, existing: dict | None = None) -> dict` —— 组合三提取器；`existing` 已填的平铺键不覆盖（`extras.*` 点号键按 `existing["extras"][suffix]` 判填）；返回本次新采集的增量 patch（不含已填字段）。

- [ ] **Step 1: 写失败测试**

`tests/test_guidance.py` 末尾追加：

```python
# ────────────────────────────────────────────────────────────────
# 审计第二批 F03：time_window 采集 + 组合抽取器（CLI/Web 共用）
# ────────────────────────────────────────────────────────────────

from fieldwork_agent.kernel.guidance.extract import (
    apply_message_extractions,
    extract_time_window,
)


def test_extract_time_window_patterns():
    assert extract_time_window("我打算2026年3月到2026年6月去调研") == {
        "time_window": "2026年3月到2026年6月"}
    assert extract_time_window("计划2026年7月出发") == {"time_window": "2026年7月"}
    assert extract_time_window("大概3月到5月之间成行") == {"time_window": "3月到5月"}
    assert extract_time_window("就9月去吧") == {"time_window": "9月"}
    assert extract_time_window("我暑假去") == {"time_window": "暑假"}
    assert extract_time_window("寒假期间过去") == {"time_window": "寒假期间"}
    assert extract_time_window("时间还没定") == {}


def test_apply_message_extractions_combines_and_respects_existing():
    existing = {"discipline": "人类学", "time_window": "暑假"}
    found = apply_message_extractions("我是社会学背景，从上海去大理调研", existing)
    # discipline 已填（人类学）不被消息里的"社会学"覆盖
    assert "discipline" not in found
    assert found.get("departure_city") == "上海"
    assert found.get("target_location") == "大理"
    # time_window 已填同样不覆盖
    assert "time_window" not in found


def test_apply_message_extractions_collects_when_empty():
    found = apply_message_extractions("社会学，2026年3月去，预算2万", {})
    assert found.get("discipline") == "社会学"
    assert found.get("time_window") == "2026年3月"
    assert found.get("budget") == "2万"


def test_apply_message_extractions_extras_dotted_key_respects_existing():
    existing = {"extras": {"region": "西南"}}
    found = apply_message_extractions("我想去华南看看", existing)
    assert "extras.region" not in found  # extras.region 已填不覆盖
    found2 = apply_message_extractions("我想去华南看看", {})
    assert found2.get("extras.region") == "华南"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_guidance.py -k "time_window or message_extractions" -v`
Expected: FAIL —— `ImportError: cannot import name 'extract_time_window'`

- [ ] **Step 3: 写实现**

(a) `kernel/guidance/extract.py` 文件顶部 import 区补 `import re`（若已有则跳过），文件末尾追加：

```python
# ---------------------------------------------------------------- 时间窗口（F03）

_TIME_WINDOW_RES: tuple = (
    re.compile(r"\d{4}年\d{1,2}月(?:到|至|-|—|~)\d{4}年\d{1,2}月"),
    re.compile(r"\d{4}年\d{1,2}月"),
    re.compile(r"\d{1,2}月(?:到|至|-|—|~)\d{1,2}月"),
    re.compile(r"\d{1,2}月"),
    re.compile(r"寒假期间|暑假期间|寒假|暑假|春假"),
)


def extract_time_window(message: str) -> dict[str, str]:
    """从消息中提取调研时间窗口（F03：time_window 是阶段 A→B 必需字段之一）。

    优先级即元组顺序：跨年月区间 > 单年月 > 月份区间 > 单月 > 假期词；
    每条消息只取第一个命中的模式（首段陈述优先，后续补充由已填过滤兜底）。
    """
    for pattern in _TIME_WINDOW_RES:
        m = pattern.search(message or "")
        if m:
            return {"time_window": m.group(0)}
    return {}


def apply_message_extractions(message: str, existing: dict | None = None) -> dict:
    """组合抽取器（CLI/Web 共用，F03 基石）：locations + researcher_info + time_window。

    返回**增量** patch：``existing`` 已填的平铺键不覆盖（与 CLI ``_apply_extract``
    原语义一致）；``extras.*`` 点号键按 ``existing["extras"][suffix]`` 判填后保留
    点号形式返回，由宿主拆分落盘。
    """
    existing = existing or {}
    extras_existing = existing.get("extras") or {}
    found: dict[str, str] = {}
    found.update(extract_locations(message, existing))
    for key, value in extract_researcher_info(message).items():
        if key.startswith("extras."):
            if not str(extras_existing.get(key.split(".", 1)[1], "")).strip():
                found[key] = value
        elif not str(existing.get(key, "")).strip():
            found[key] = value
    for key, value in extract_time_window(message).items():
        if not str(existing.get(key, "")).strip():
            found[key] = value
    return found
```

(b) `core/orchestrator.py`：L34 的 import 改为 `from fieldwork_agent.kernel.guidance.extract import apply_message_extractions`；`_apply_extract`（L259-280）方法体替换为：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_guidance.py tests/test_loop_orchestrator.py -v`
Expected: 全部 PASS（既有 CLI 抽取行为不变——`test_loop_orchestrator.py` 的抽取用例仍绿）。

- [ ] **Step 5: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 361+ passed, 1 skipped（数量不低于第一批基线，新增用例计入）。

- [ ] **Step 6: 提交**

```
git add kernel/guidance/extract.py core/orchestrator.py tests/test_guidance.py
```
Commit message: `feat(kernel): 抽取下沉——extract_time_window + apply_message_extractions 组合器（审计 F03 基石）`

---

### Task 2: proposal NA 豁免——非访谈研究的文档生成一致（F12）

**Files:**
- Modify: `kernel/documents/proposal.py:122-142`（`_bullets_informants` 后新增共享 helper）、`L158-187`（`build_context_block`）、`L193-226`（`_degrade_markdown`）
- Test: `tests/test_documents.py`（`SITES` 常量后追加 `NA_CTX`，文件末尾追加用例）

**Interfaces:**
- Consumes: `_bullets_informants(items)`（本文件既有）；`ResearchContext.interviews_not_applicable` / `interviews_na_reason`（`kernel.models` 已有，phases.py 已用）。
- Produces: `_informants_bullets(items: list[dict], ctx: ResearchContext) -> list[str]` —— 模块内私有 helper，`build_context_block` 与 `_degrade_markdown` 共用；对外签名不变。

- [ ] **Step 1: 写失败测试**

`tests/test_documents.py` 在 `SITES = ...` 行后追加常量，文件末尾追加用例：

```python
# 审计第二批 F12：非访谈研究（interviews_not_applicable）豁免访谈渠道硬条件
NA_CTX = ResearchContext(
    discipline="社会学", topic="历史文献研究", scale="单人调研",
    budget="2万", time_window="6-9月", target_location="喜洲",
    interviews_not_applicable=True,
    interviews_na_reason="采用档案与文献分析法，不需访谈对象")
```

```python
def test_context_block_na_exempts_informant_channel():
    block = build_context_block(NA_CTX, SITES, {"legs": []}, [], [])
    assert "不适用访谈方法" in block
    assert "档案与文献分析法" in block


def test_context_block_still_raises_without_na_flag():
    with pytest.raises(ValueError, match="缺少渠道"):
        build_context_block(CTX, SITES, {"legs": []}, [], [])  # informants 为空且未声明 NA


def test_degrade_na_exempts_informant_section():
    doc = compose_proposal(NA_CTX, SITES, {"legs": []}, [], [], llm=None)
    assert len(doc.sections) == 8  # 八章降级版照常产出
    assert "不适用访谈方法" in doc.markdown
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_documents.py -k "na_" -v`
Expected: FAIL —— 前两个 `ValueError` 未被豁免（`pytest.raises` 得不到异常）；第三个同因炸出。

- [ ] **Step 3: 写实现**

`kernel/documents/proposal.py`：

(a) `_bullets_informants`（L122-142）函数后新增：

```python
def _informants_bullets(items: list, ctx) -> list[str]:
    """访谈对象 bullets（审计 F12）：非访谈研究豁免空候选，其余沿用既有渲染。"""
    if items:
        return _bullets_informants(items)
    if getattr(ctx, "interviews_not_applicable", False):
        reason = getattr(ctx, "interviews_na_reason", "") or "研究者已声明本研究不适用访谈方法"
        return [f"- 不适用访谈方法：{reason}"]
    return _bullets_informants(items)  # 空且未声明 NA → 既有占位（raise 由调用方把关）
```

(b) `build_context_block`（L158-187）中 informants 校验行（现 `if not informants: raise ValueError("缺少渠道: 访谈对象")`）改为：

```python
    if not informants and not getattr(ctx, "interviews_not_applicable", False):
        raise ValueError("缺少渠道: 访谈对象")
```

且 blocks 列表的访谈对象分节改用 `_informants_bullets(informants, ctx)`。

(c) `_degrade_markdown`（L193-226）中 bodies 的访谈对象段（现 `_bullets_informants(informant_items)`）改用 `_informants_bullets(informant_items, ctx)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_documents.py -v`
Expected: 全部 PASS（既有 `test_channel_absent_asserts`/`test_degrade_without_llm` 等不受影响）。

- [ ] **Step 5: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest`

- [ ] **Step 6: 提交**

```
git add kernel/documents/proposal.py tests/test_documents.py
```
Commit message: `feat(kernel): proposal 非访谈研究 NA 豁免——访谈渠道校验与降级渲染一致（审计 F12）`

---

### Task 3: repo 确认基础设施 + 按 ID 读文档（F04/F13 数据层）

**Files:**
- Modify: `kernel/repository/json_repo.py:46-67`（`_default_state` 补键）、import 区（L23-29 后加 models import）、文件末尾（L454 后追加四方法）
- Modify: `kernel/repository/protocol.py:131-146`（提案文档区文档字符串补四个新方法声明——Protocol 本体不加方法签名，仅文档说明"确认族方法见 JsonRepository，后续实现必须逐条一致"亦可；采用加签名方式则一并加）
- Test: `tests/repository_contract.py`（提案文档区后追加用例）、`tests/test_json_repo.py`（末尾追加）

**Interfaces:**
- Consumes: `ConfirmationRecord` / `compute_inputs_hash` / `inputs_stale`（`kernel.models` 已有）；`_now()`（本文件 L37）；`list_proposals(pid)`（本文件既有）。
- Produces（Task 6/7/11 消费，签名精确）:
  - `confirm_field_site(pid: str, site_id: str) -> bool` —— 按 `id` 置 `confirmed=True`；未知 id 返回 `False`；已确认幂等返回 `True`。
  - `confirm_proposal(pid: str, subject: str = "") -> dict` —— 落一条 `ConfirmationRecord`（`inputs_hash` = `_CONFIRM_INPUT_FIELDS` 六字段当前值哈希）并置 `proposal_confirmed=True`；返回 `record.model_dump(mode="json")`。
  - `get_proposal_by_id(pid: str, doc_id: str) -> dict | None` —— 扫 `list_proposals` 按 `id` 取（含 `content` 正文，天然支持 `legacy_` 前缀）；不存在返回 `None`。
  - `is_proposal_confirmation_stale(pid: str) -> bool` —— 无确认记录返回 `False`；有记录取**最近一条**的 `inputs_hash` 与当前输入哈希比 `inputs_stale`。

- [ ] **Step 1: 写失败测试**

`tests/test_json_repo.py` 末尾追加：

```python
# ────────────────────────────────────────────────────────────────
# 审计第二批 F04/F13：确认基础设施 + 按 ID 读文档
# ────────────────────────────────────────────────────────────────

def test_confirm_field_site_by_id(json_repo):
    sid = json_repo.save_field_site("p1", {"name": "喜洲", "confirmed": False})
    assert json_repo.confirm_field_site("p1", sid) is True
    assert json_repo.list_field_sites("p1")[0]["confirmed"] is True
    assert json_repo.confirm_field_site("p1", "no_such") is False
    assert json_repo.confirm_field_site("p1", sid) is True  # 幂等


def test_confirm_proposal_records_hash(json_repo):
    json_repo.update_research_context("p1", {"budget": "2万", "time_window": "暑假"})
    json_repo.save_proposal("p1", "# 方案", kind="proposal", version="final")
    record = json_repo.confirm_proposal("p1")
    assert record["kind"] == "proposal" and record["inputs_hash"]
    state = _read_state(json_repo, "p1")
    assert state["proposal_confirmed"] is True
    assert len(state["confirmations"]) == 1


def test_confirmation_stale_after_budget_change(json_repo):
    json_repo.save_proposal("p1", "# 方案")
    json_repo.confirm_proposal("p1")
    assert json_repo.is_proposal_confirmation_stale("p1") is False
    json_repo.update_research_context("p1", {"budget": "3万"})
    assert json_repo.is_proposal_confirmation_stale("p1") is True


def test_stale_false_without_confirmation(json_repo):
    assert json_repo.is_proposal_confirmation_stale("p1") is False


def test_get_proposal_by_id_roundtrip(json_repo):
    pid = json_repo.save_proposal("p1", "正文A", version="draft")
    json_repo.save_proposal("p1", "正文B", version="draft")
    doc = json_repo.get_proposal_by_id("p1", pid)
    assert doc is not None and doc["content"] == "正文A"
    assert json_repo.get_proposal_by_id("p1", "no_such") is None
```

`tests/repository_contract.py` 提案文档区（`test_no_proposal_returns_none` 后）追加：

```python
    # ---------------------------------------------------------------- 确认与失效（第二批 F04）

    def test_confirm_and_stale_contract(self, repo):
        sid = repo.save_field_site("p1", {"name": "喜洲", "confirmed": False})
        assert repo.confirm_field_site("p1", sid) is True
        repo.update_research_context("p1", {"budget": "2万"})
        repo.save_proposal("p1", "# 方案", version="final")
        repo.confirm_proposal("p1")
        assert repo.is_proposal_confirmation_stale("p1") is False
        doc_id = repo.get_latest_proposal("p1")["id"]
        assert repo.get_proposal_by_id("p1", doc_id)["content"] == "# 方案"
        repo.update_research_context("p1", {"budget": "5万"})
        assert repo.is_proposal_confirmation_stale("p1") is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_json_repo.py tests/repository_contract.py -k "confirm or stale or by_id" -v`
Expected: FAIL —— `AttributeError: 'JsonRepository' object has no attribute 'confirm_field_site'`

- [ ] **Step 3: 写实现**

`kernel/repository/json_repo.py`：

(a) import 区（`from fieldwork_agent.kernel.guidance.phases import PHASES` 后）加：

```python
from fieldwork_agent.kernel.models import ConfirmationRecord, compute_inputs_hash, inputs_stale
```

(b) 常量区（`CACHE_LIMIT_BYTES` 后）加：

```python
# 方案确认的关键输入字段（审计 F04：这些字段变更后旧确认失效）
CONFIRM_INPUT_FIELDS = ("travel_date", "budget", "time_window",
                        "departure_city", "target_location", "scale")
```

(c) `_default_state`（L46-67）的 `"proposals": []` 行后补两键：

```python
            "proposals": [],
            "confirmations": [],
            "proposal_confirmed": False,
```

(d) 文件末尾（`get_latest_proposal` 后）追加四方法：

```python
    # ---------------------------------------------------------------- 确认与失效（第二批 F04）

    def confirm_field_site(self, pid: str, site_id: str) -> bool:
        """按记录 id 把田野点置为已确认；未知 id 返回 False（幂等）。"""
        state = self._load(pid)
        for site in state["field_sites"]:
            if site.get("id") == site_id:
                site["confirmed"] = True
                state["updated_at"] = _now()
                self._save(pid, state)
                return True
        return False

    def confirm_proposal(self, pid: str, subject: str = "") -> dict:
        """记录一次方案确认：落 ConfirmationRecord（含关键输入哈希）并置 proposal_confirmed。"""
        state = self._load(pid)
        ctx = state.get("research_context") or {}
        payload = {f: str(ctx.get(f, "") or "") for f in CONFIRM_INPUT_FIELDS}
        latest_id = state["proposals"][-1]["id"] if state.get("proposals") else ""
        record = ConfirmationRecord(
            kind="proposal", subject=subject or latest_id,
            confirmed_at=_now(), inputs_hash=compute_inputs_hash(payload))
        state.setdefault("confirmations", []).append(record.model_dump(mode="json"))
        state["proposal_confirmed"] = True
        state["updated_at"] = record.confirmed_at
        self._save(pid, state)
        return record.model_dump(mode="json")

    def get_proposal_by_id(self, pid: str, doc_id: str) -> dict | None:
        """按记录 id 取文档（含 content 正文，兼容 legacy_ 前缀）；不存在返回 None。"""
        for record in self.list_proposals(pid):
            if record.get("id") == doc_id:
                return record
        return None

    def is_proposal_confirmation_stale(self, pid: str) -> bool:
        """最近一次方案确认的输入哈希与当前不一致 → True；无确认记录返回 False。"""
        records = self._load(pid).get("confirmations") or []
        if not records:
            return False
        ctx = self._load(pid).get("research_context") or {}
        payload = {f: str(ctx.get(f, "") or "") for f in CONFIRM_INPUT_FIELDS}
        return inputs_stale(records[-1].get("inputs_hash", ""), compute_inputs_hash(payload))
```

(e) `kernel/repository/protocol.py` 提案文档区（`list_proposals` 声明后）追加四个方法声明（同既有 `...` 体风格，docstring 写明语义）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_json_repo.py tests/repository_contract.py -v`

- [ ] **Step 5: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest`

- [ ] **Step 6: 提交**

```
git add kernel/repository/json_repo.py kernel/repository/protocol.py tests/test_json_repo.py tests/repository_contract.py
```
Commit message: `feat(kernel): repo 确认基础设施——田野点/方案确认、按 ID 读文档、确认失效判定（审计 F04/F13）`

---

### Task 4: Web 回合三修——抽取写回、文档落盘、消息去重（F03/F04/F09）

**Files:**
- Modify: `web_host/agent/loop.py:31-33`（F09 交换）、`L120-127`（轮末插 _apply_extractions）、`L165-186`（_persist_tool_results 补两分支）、`L188-206`（_check_and_advance 改名公共）、文件末尾追加 `_apply_extractions`
- Test: `tests/test_web_agent_loop.py`（FakeFlyAI 类后追加 ChatFakeLLM，文件末尾追加 4 用例）

**Interfaces:**
- Consumes: `apply_message_extractions(message, existing) -> dict`（Task 1 产出）；`repo.save_proposal(pid, markdown, *, kind, version)` / `repo.get_proposal_by_id(pid, doc_id)`（Task 3 产出）；`build_messages(repo, pid, user_text, phase)`（`web_host/agent/context.py:31-40`——L35 读历史、L39 append 当前消息，故先 build 后 append 才不重复）。
- Produces: `check_and_advance(repo, pid) -> dict | None`（原 `_check_and_advance` 去下划线转公共，供 Task 6 的 API 层 import）。

- [ ] **Step 1: 写失败测试**

`tests/test_web_agent_loop.py` 在 `class FakeFlyAI:` 块之后追加：

```python
class ChatFakeLLM(FakeLLM):
    """带非流式 chat() 的 Fake：compose_proposal/compose_interview_guide
    走 llm.chat()（kernel.llm 契约）；返回空 content → 触发模板降级路径，
    测试不依赖真实 LLM。"""
    def chat(self, messages, **kw):
        return {"content": ""}


# F04：compose_proposal 完整四硬渠道参数（独立于 Task 2 的 NA 豁免路径）
_PROPOSAL_ARGS = {
    "ctx": {"discipline": "社会学", "topic": "宗族变迁", "scale": "单人调研",
            "budget": "2万", "time_window": "7月", "departure_city": "上海",
            "target_location": "大理"},
    "sites": [{"name": "大理", "confirmed": True, "profile": {"location": "大理"}}],
    "itinerary": {"legs": []},
    "informants": [{"code": "INF-01", "type": "宗族长老",
                    "relation": "同乡会引荐", "contact": "", "notes": ""}],
}
```

文件末尾追加用例：

```python
def test_user_message_not_duplicated_to_llm(env):
    """F09：当前用户消息只发一次给模型（不随 history 重复）。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "ok"}, {"content": "ok", "tool_calls": None,
                    "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "我要去大理", tm, llm, FakeFlyAI()))
    msgs = llm.calls[0][0]
    dup = [m for m in msgs if m["role"] == "user" and m["content"] == "我要去大理"]
    assert len(dup) == 1


def test_extraction_persists_research_context(env):
    """F03：对话中的研究信息轮末写回结构化状态（确定性抽取打底）。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "好"}, {"content": "好", "tool_calls": None,
                    "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id,
                         "我是社会学背景，从上海去大理调研", tm, llm, FakeFlyAI()))
    ctx = repo.get_research_context("p1")
    assert ctx.get("discipline") == "社会学"
    assert ctx.get("departure_city") == "上海"
    assert ctx.get("target_location") == "大理"


def test_compose_proposal_persisted_as_document(env):
    """F04：compose_proposal 成功后文档落盘（kind/version 如实记录）。"""
    repo, tm = env
    llm = ChatFakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "compose_proposal",
                                          "arguments": _PROPOSAL_ARGS}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已生成"},
         {"content": "已生成", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "生成方案", tm, llm, FakeFlyAI()))
    docs = repo.list_proposals("p1")
    assert len(docs) == 1
    assert docs[0]["kind"] == "proposal"
    doc = repo.get_proposal_by_id("p1", docs[0]["id"])
    assert doc["content"].startswith("# ")
    assert "## " in doc["content"]  # 八章降级结构


def test_compose_interview_guide_persisted(env):
    """F04：compose_interview_guide 的产物拼合落盘为 interview_guide 文档。"""
    repo, tm = env
    llm = ChatFakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "compose_interview_guide",
                                          "arguments": {"topic": "宗族变迁",
                                                        "informant_types": ["宗族长老"]}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已生成"},
         {"content": "已生成", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "生成访谈提纲", tm, llm, FakeFlyAI()))
    docs = repo.list_proposals("p1")
    assert len(docs) == 1
    assert docs[0]["kind"] == "interview_guide"
    doc = repo.get_proposal_by_id("p1", docs[0]["id"])
    assert "## 宗族长老" in doc["content"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_agent_loop.py -k "not_duplicated or extraction_persists or proposal_persisted or interview_guide_persisted" -v`
Expected: 4 FAIL —— ①重复（len(dup)==2）；②ctx 空 KeyError；③④`list_proposals` 为空 AssertionError。既有 6 用例仍绿（`test_user_message_persisted_immediately` 不受交换影响：消息仍落盘一次）。

- [ ] **Step 3: 写实现**

`web_host/agent/loop.py` 四处修改：

(a) **F09**：L30-33 替换为（先构建再落盘——`build_messages` 的 L35 读历史、L39 自行 append 当前消息，先 append 会把当前消息读进历史再追加一次）：

```python
    # R4：用户消息即时持久化（F09 修正：先 build 后 append——
    # build_messages 读 history 后自行 append 当前消息，顺序颠倒会双发）
    messages = build_messages(repo, pid, user_text, phase)
    repo.append_message(pid, "user", user_text)
```

(b) **F04**：`_persist_tool_results` 的 `elif name == "compose_itinerary":` 分支（L183-185）之后追加：

```python
        elif name == "compose_proposal":
            markdown = data_dict.get("markdown", "")
            if markdown:
                repo.save_proposal(pid, markdown,
                                    kind=data_dict.get("kind") or "proposal",
                                    version=data_dict.get("version") or "draft")
        elif name == "compose_interview_guide":
            # guide 返回 {类型: 正文} → 拼单文档落盘
            parts = ["# 访谈提纲", ""]
            for itype, body in (data_dict or {}).items():
                if not str(body).strip():
                    continue
                parts += [f"## {itype}", "", str(body), ""]
            if len(parts) > 2:
                repo.save_proposal(pid, "\n".join(parts),
                                   kind="interview_guide", version="draft")
```

(c) **改名**：`def _check_and_advance` → `def check_and_advance`（docstring 补一句「公共：供 web_host.api 复用」）；L125 调用处同步改名。

(d) **F03**：文件末尾（`check_and_advance` 之后）追加新函数：

```python
def _apply_extractions(repo, pid, user_text):
    """轮末确定性抽取（F03）：对话中的研究信息写回结构化状态。

    复用 CLI 同款 apply_message_extractions（增量语义：已填字段不覆盖、
    extras.* 点号键并入旧 extras 合并落盘）。
    """
    from fieldwork_agent.kernel.guidance.extract import apply_message_extractions
    ctx = repo.get_research_context(pid)
    found = apply_message_extractions(user_text, ctx)
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
    repo.update_research_context(pid, merged)
```

轮末（L120-127 区）在 `_persist_tool_results(...)` 与 `transition = ...` 之间插一行：

```python
    _persist_tool_results(repo, pid, tm, turn_id, call_name_map)
    _apply_extractions(repo, pid, user_text)
    transition = check_and_advance(repo, pid)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_agent_loop.py -v`
Expected: 全部 PASS（10 用例）。

- [ ] **Step 5: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 不低于 Task 3 基线 +4。

- [ ] **Step 6: 提交**

```
git add web_host/agent/loop.py tests/test_web_agent_loop.py
```
Commit message: `feat(web): 回合三修——抽取写回/文档落盘/消息去重，check_and_advance 转公共（审计 F03/F04/F09）`

---

### Task 5: Web 回合受限状态写工具——update_research_context（F03 方案②）

**Files:**
- Create: `web_host/agent/state_tools.py`
- Modify: `web_host/agent/loop.py:27-28`（接线 schemas）、`L99`（_exec_tool 传 local_handlers）、`L160-162`（_exec_tool 签名加参数）
- Test: `tests/test_web_state_tools.py`（新建）

**Interfaces:**
- Consumes: `repo.get_research_context / update_research_context`；`Envelope / ToolError`（kernel.models）。
- Produces: `build_state_tools(repo, pid) -> tuple[list[dict], dict[str, Callable]]`——schemas 为 OpenAI function 格式（同 `build_tool_schemas()` 元素结构），handlers 收具名参数返回 `Envelope`。模块级常量 `ALLOWED_CONTEXT_FIELDS`（12 键白名单）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_web_state_tools.py`：

```python
# tests/test_web_state_tools.py
"""Web 回合受限状态写工具（审计 F03 方案②）：白名单过滤 + 回路集成。"""
import asyncio

import pytest
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.agent.state_tools import build_state_tools
from fieldwork_agent.web_host.turn_manager import TurnManager


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat_stream(self, messages, **kw):
        self.calls.append((list(messages), kw))
        return iter(self.script.pop(0))


class FakeFlyAI:
    def __getattr__(self, name):
        def _stub(**kw):
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name=f"{name}-结果", price=100.0)],
                total=1, cheapest=100.0, dearest=100.0))
        return _stub


@pytest.fixture
def env(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    tm = TurnManager(root=tmp_path / "projects")
    return repo, tm


def test_whitelist_filters_and_persists(env):
    repo, _ = env
    schemas, handlers = build_state_tools(repo, "p1")
    assert [s["function"]["name"] for s in schemas] == ["update_research_context"]
    envl = handlers["update_research_context"](
        patch={"budget": "5万", "hacked": "x", "topic": "  "})
    assert envl.ok
    assert envl.data == {"updated": ["budget"]}
    assert repo.get_research_context("p1").get("budget") == "5万"


def test_bool_flag_persists(env):
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](
        patch={"interviews_not_applicable": True,
               "interviews_na_reason": "纯文献研究"})
    assert envl.ok
    ctx = repo.get_research_context("p1")
    assert ctx.get("interviews_not_applicable") is True
    assert ctx.get("interviews_na_reason") == "纯文献研究"


def test_empty_patch_rejected(env):
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](patch={"nope": 1})
    assert not envl.ok
    assert envl.error.code == "invalid_arguments"


def test_loop_state_tool_call_persists(env):
    """回路集成：LLM 发起 update_research_context 工具调用 → 状态落盘。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "update_research_context",
                                          "arguments": {"patch": {"budget": "5万",
                                                                   "scale": "两人同行"}}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已更新"},
         {"content": "已更新", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "预算改成5万，两人同行",
                         tm, llm, FakeFlyAI()))
    ctx = repo.get_research_context("p1")
    assert ctx.get("budget") == "5万" and ctx.get("scale") == "两人同行"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_state_tools.py -v`
Expected: 全部 ERROR/FAIL —— `ModuleNotFoundError: No module named 'fieldwork_agent.web_host.agent.state_tools'`。

- [ ] **Step 3: 写实现**

新建 `web_host/agent/state_tools.py`：

```python
# web_host/agent/state_tools.py
"""Web 回合受限状态写工具（审计 F03 方案②）：update_research_context。

与 mcp_server/state_tools.py 同构的宿主层工具：字段白名单 + 值校验 +
Envelope 包络；区别在于绑定单一项目 pid（loop 每回合构建，不入全局
registry——kernel 能力保持只读，状态写只在宿主层发生）。
"""
from fieldwork_agent.kernel.models import Envelope, ToolError

ALLOWED_CONTEXT_FIELDS = (
    "discipline", "topic", "site_status", "scale", "budget",
    "time_window", "departure_city", "target_location", "travel_date",
    "purpose", "interviews_not_applicable", "interviews_na_reason",
)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_research_context",
        "description": (
            "把对话中用户明确确认的研究信息写入项目状态（仅白名单字段）。"
            "禁止推测、禁止补默认值；修正用完整新值覆盖（如预算 2 万改 5 万）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "object",
                    "description": "要写入的字段 → 新值（白名单外键静默忽略）",
                },
                "reason": {
                    "type": "string",
                    "description": "一句话说明依据（如「用户更正预算为 5 万」）",
                },
            },
            "required": ["patch"],
        },
    },
}


def build_state_tools(repo, pid: str):
    """构建绑定 pid 的受限状态写工具：返回 (schemas, handlers)。"""

    def update_research_context(patch: dict, reason: str = "") -> Envelope:
        clean = {}
        for key, value in (patch or {}).items():
            if key not in ALLOWED_CONTEXT_FIELDS:
                continue
            if isinstance(value, bool):
                clean[key] = value
            elif isinstance(value, str) and value.strip():
                clean[key] = value.strip()
        if not clean:
            return Envelope(ok=False, error=ToolError(
                code="invalid_arguments",
                message="patch 为空：至少一个白名单字段且值为非空字符串或布尔",
                hint="可写白名单：" + "、".join(ALLOWED_CONTEXT_FIELDS)))
        ctx = dict(repo.get_research_context(pid))
        ctx.update(clean)
        repo.update_research_context(pid, ctx)
        return Envelope(ok=True, data={"updated": sorted(clean)})

    return [_SCHEMA], {"update_research_context": update_research_context}
```

`web_host/agent/loop.py` 三处接线：

(a) L27-28 之后（`tools = build_tool_schemas()` 与 `phase = ...` 之间无依赖，放在 `dispatch = ...` 行后均可，紧邻 tools 行最直观）：

```python
    dispatch = build_dispatch_fn(flyai_client, llm)
    tools = build_tool_schemas()
    state_schemas, state_handlers = build_state_tools(repo, pid)
    tools = tools + state_schemas
```

顶部 import 区追加 `from fieldwork_agent.web_host.agent.state_tools import build_state_tools`。

(b) L99 调用处：

```python
                coros.append(_exec_tool(dispatch, name, args, call_id, state_handlers))
```

(c) L160-162 `_exec_tool` 签名与首行：

```python
async def _exec_tool(dispatch, name, args, call_id, local_handlers):
    if name in local_handlers:
        env = local_handlers[name](**args)
    else:
        env = await asyncio.to_thread(dispatch, name, args)
    return (call_id, name, env)
```

（state handler 是同步纯内存/JSON 落盘，直调不占线程池；`_persist_tool_results` 的轮末 replay 对该 name 无分支 → 不二次落盘。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_state_tools.py tests/test_web_agent_loop.py -v`
Expected: 全部 PASS（既有 10 用例不受接线影响——普通工具路径不变）。

- [ ] **Step 5: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 不低于 Task 4 基线 +4。

- [ ] **Step 6: 提交**

```
git add web_host/agent/state_tools.py web_host/agent/loop.py tests/test_web_state_tools.py
```
Commit message: `feat(web): 回合受限状态写工具 update_research_context——白名单+回路接线（审计 F03）`

---

### Task 6: 状态修正与确认 API——修正/确认/采纳/方案确认＋stale 接线（F03/F04）

**Files:**
- Create: `web_host/api/state.py`（四端点）、`tests/test_web_state_api.py`
- Modify: `web_host/app.py:106`（imports 加 state）、`L108-111`（include_router 加一行）；`web_host/api/projects.py:52-60`（get_project 响应加两键）；`web_host/agent/loop.py:198-201`（check_and_advance 内 stale 接线）；`kernel/documents/informants.py`（末尾加 candidate_to_record）

**Interfaces:**
- Consumes: `repo.confirm_field_site(pid, site_id) -> bool`（True=确认/幂等，False=未知 id）/ `repo.save_informant(pid, record) -> str` / `repo.confirm_proposal(pid, subject="") -> dict` / `repo.is_proposal_confirmation_stale(pid) -> bool`（均 Task 3 产出）；`check_and_advance(repo, pid)`（Task 4 转公共）；`InformantCandidate`（kernel.models，七字段全默认值）。
- Produces: ①`PUT /projects/{id}/research-context`（body: ContextPatch 12 字段全 optional）→ `{research_context, phase_transition}`；②`POST /projects/{id}/field-sites/{site_id}/confirm` → `{field_site, phase_transition}`；③`POST /projects/{id}/informants`（body: `{candidate: dict}`）→ 201 `{informant(含 id), phase_transition}`；④`POST /projects/{id}/proposal/confirm` → `{confirmation, proposal_confirmed, phase_transition}`。`candidate_to_record(candidate: InformantCandidate, seq: int) -> dict`（kernel.documents.informants 末尾）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_web_state_api.py`（fixture 照抄 test_web_projects_api.py:7-23；用例内经 `app.state.repo` 预置状态）：

```python
# tests/test_web_state_api.py
"""状态修正与确认 API（审计第二批 F03/F04）：修正/确认/采纳/方案确认+stale。"""
import pytest
from fieldwork_agent.web_host.app import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    _app = create_app(config=None, repo=repo)
    # ASGITransport 不触发 lifespan，手动初始化依赖
    init_deps(_app, config=None, repo=repo)
    return _app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_update_research_context(client):
    r = await client.put("/projects/p1/research-context",
                         json={"budget": "5万", "scale": "两人同行"})
    assert r.status_code == 200
    ctx = r.json()["research_context"]
    assert ctx["budget"] == "5万" and ctx["scale"] == "两人同行"


async def test_update_research_context_empty_400(client):
    r = await client.put("/projects/p1/research-context", json={})
    assert r.status_code == 400


async def test_update_research_context_unknown_404(client):
    r = await client.put("/projects/ghost/research-context", json={"budget": "5万"})
    assert r.status_code == 404


async def test_confirm_field_site(client, app):
    repo = app.state.repo
    sid = repo.save_field_site("p1", {"name": "大理", "confirmed": False,
                                      "profile": {"location": "大理"}})
    r = await client.post(f"/projects/p1/field-sites/{sid}/confirm")
    assert r.status_code == 200
    assert r.json()["field_site"]["confirmed"] is True
    r2 = await client.post("/projects/p1/field-sites/nope/confirm")
    assert r2.status_code == 404


async def test_adopt_informant_seq_codes(client):
    cand = {"type": "宗族长老", "approach": "同乡会引荐", "cautions": "避免直接问族产",
            "question_domains": ["族谱", "祭祀"], "ethics": "匿名处理",
            "access_channel": "村委会介绍", "preparation": "先读村志"}
    r = await client.post("/projects/p1/informants", json={"candidate": cand})
    assert r.status_code == 201
    inf = r.json()["informant"]
    assert inf["code"] == "INF-01" and inf["id"]
    assert "匿名处理" in inf["notes"]
    r2 = await client.post("/projects/p1/informants", json={"candidate": cand})
    assert r2.status_code == 201
    assert r2.json()["informant"]["code"] == "INF-02"


async def test_adopt_informant_type_error_400(client):
    r = await client.post("/projects/p1/informants",
                          json={"candidate": {"question_domains": "不是列表"}})
    assert r.status_code == 400


async def test_confirm_proposal_requires_document(client):
    r = await client.post("/projects/p1/proposal/confirm")
    assert r.status_code == 400


async def test_confirm_proposal_flow_and_stale(client, app):
    repo = app.state.repo
    repo.save_proposal("p1", "# 研究方案\n\n## 一、背景\n\n内容")
    r = await client.post("/projects/p1/proposal/confirm")
    assert r.status_code == 200
    assert r.json()["proposal_confirmed"] is True
    snap = (await client.get("/projects/p1")).json()
    assert snap["proposal_confirmed"] is True
    assert snap["proposal_confirmation_stale"] is False
    # 修改预算 → 确认失效（确认级失效：inputs_hash 不一致）
    await client.put("/projects/p1/research-context", json={"budget": "9万"})
    snap2 = (await client.get("/projects/p1")).json()
    assert snap2["proposal_confirmation_stale"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_state_api.py -v`
Expected: 全部 404/405 FAIL——路由不存在（PUT/新 POST 路径均未注册；`test_confirm_proposal_flow_and_stale` 里 `proposal_confirmed` 键也不在快照响应）。

- [ ] **Step 3: 写实现**

(a) `kernel/documents/informants.py` 末尾追加（顶部若无 models import 则补 `from fieldwork_agent.kernel.models import InformantCandidate`）：

```python
def candidate_to_record(candidate: InformantCandidate, seq: int) -> dict:
    """候选档案 → 报道人记录（审计 F04）：七字段映射到 InformantRecord 五字段。

    code 按登记序号编号（INF-01 起）；approach→relation、access_channel→contact；
    cautions/ethics/preparation/question_domains 并入 notes（「准备/注意/伦理/提问域」前缀）。
    """
    notes_parts = []
    if candidate.preparation:
        notes_parts.append(f"准备：{candidate.preparation}")
    if candidate.cautions:
        notes_parts.append(f"注意：{candidate.cautions}")
    if candidate.ethics:
        notes_parts.append(f"伦理：{candidate.ethics}")
    if candidate.question_domains:
        notes_parts.append("提问域：" + "、".join(candidate.question_domains))
    return {"code": f"INF-{seq:02d}", "type": candidate.type,
            "relation": candidate.approach, "contact": candidate.access_channel,
            "notes": "；".join(notes_parts)}
```

(b) 新建 `web_host/api/state.py`：

```python
# web_host/api/state.py
"""项目状态修正与确认路由（审计第二批 F03/F04）：研究上下文修正、
田野点确认、报道人采纳、方案确认。守卫先于一切副作用；每次写后跑
确定性阶段推进，把 phase_transition 原样返回给前端。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from fieldwork_agent.kernel.documents.informants import candidate_to_record
from fieldwork_agent.kernel.models import InformantCandidate
from fieldwork_agent.web_host.agent.loop import check_and_advance
from fieldwork_agent.web_host.api.guard import checked_project_id
from fieldwork_agent.web_host.deps import get_repo

router = APIRouter(tags=["state"])


class ContextPatch(BaseModel):
    discipline: str | None = None
    topic: str | None = None
    site_status: str | None = None
    scale: str | None = None
    budget: str | None = None
    time_window: str | None = None
    departure_city: str | None = None
    target_location: str | None = None
    travel_date: str | None = None
    purpose: str | None = None
    interviews_not_applicable: bool | None = None
    interviews_na_reason: str | None = None


class AdoptInformantRequest(BaseModel):
    candidate: dict


def _guarded_state(repo, project_id: str) -> None:
    checked_project_id(project_id, repo)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None


def _transition(repo, project_id: str):
    t = check_and_advance(repo, project_id)
    return {"from": t["from"], "to": t["to"]} if t else None


@router.put("/projects/{project_id}/research-context")
async def update_research_context(project_id: str, body: ContextPatch,
                                  repo=Depends(get_repo)):
    _guarded_state(repo, project_id)
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items()
             if v is not None and (isinstance(v, bool) or str(v).strip())}
    if not patch:
        raise HTTPException(400, "至少提供一个要修正的字段（非空字符串或布尔）")
    ctx = dict(repo.get_research_context(project_id))
    ctx.update(patch)
    repo.update_research_context(project_id, ctx)
    return {"research_context": repo.get_research_context(project_id),
            "phase_transition": _transition(repo, project_id)}


@router.post("/projects/{project_id}/field-sites/{site_id}/confirm")
async def confirm_field_site(project_id: str, site_id: str, repo=Depends(get_repo)):
    _guarded_state(repo, project_id)
    if not repo.confirm_field_site(project_id, site_id):
        raise HTTPException(404, f"田野点不存在: {site_id}")
    record = next((s for s in repo.list_field_sites(project_id)
                   if s.get("id") == site_id), {})
    return {"field_site": record,
            "phase_transition": _transition(repo, project_id)}


@router.post("/projects/{project_id}/informants", status_code=201)
async def adopt_informant(project_id: str, body: AdoptInformantRequest,
                          repo=Depends(get_repo)):
    _guarded_state(repo, project_id)
    try:
        candidate = InformantCandidate.model_validate(body.candidate)
    except ValidationError as exc:
        raise HTTPException(400, f"候选档案不合法: {exc.errors()[0]['msg']}") from None
    seq = len(repo.list_informants(project_id)) + 1
    record = candidate_to_record(candidate, seq)
    rid = repo.save_informant(project_id, record)
    return {"informant": {**record, "id": rid},
            "phase_transition": _transition(repo, project_id)}


@router.post("/projects/{project_id}/proposal/confirm")
async def confirm_proposal(project_id: str, repo=Depends(get_repo)):
    _guarded_state(repo, project_id)
    if not repo.list_proposals(project_id):
        raise HTTPException(400, "尚无方案文档：先生成方案再确认")
    confirmation = repo.confirm_proposal(project_id)
    return {"confirmation": confirmation, "proposal_confirmed": True,
            "phase_transition": _transition(repo, project_id)}
```

(c) `web_host/app.py` L106 与 L108-111：

```python
    from fieldwork_agent.web_host.api import exports, projects, settings, state, turns

    app.include_router(projects.router)
    app.include_router(turns.router)
    app.include_router(exports.router)
    app.include_router(settings.router)
    app.include_router(state.router)
```

(d) `web_host/api/projects.py` get_project 响应（L52-60）补两键：

```python
    return {"project_id": project_id, "phase": state["phase"],
            "research_context": repo.get_research_context(project_id),
            "field_sites": repo.list_field_sites(project_id),
            "informants": repo.list_informants(project_id),
            "proposals": [{"id": p["id"], "kind": p.get("kind"),
                           "version": p.get("version"), "created_at": p.get("created_at")}
                          for p in repo.list_proposals(project_id)],
            "phase_history": state.get("phase_history", []),
            "updated_at": state.get("updated_at"),
            "proposal_confirmed": bool(state.get("proposal_confirmed")),
            "proposal_confirmation_stale": repo.is_proposal_confirmation_stale(project_id)}
```

（created_at 口径 bug 留 Task 7 统一修，本任务不动该行。）

(e) `web_host/agent/loop.py` check_and_advance 内（原 L200-201）：

```python
    confirmed = (bool(state.get("proposal_confirmed"))
                 and not repo.is_proposal_confirmation_stale(pid))
    result = phases.check_advance(
        state["phase"], ctx, sites, informants,
        has_itinerary, bool(state.get("proposals")), confirmed)
```

（stale 的确认不支撑 finished；已 finished 不回退——前端以 stale 标记呈现，用户重新确认后再次推进。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_state_api.py tests/test_web_projects_api.py tests/test_web_agent_loop.py -v`
Expected: 全部 PASS（快照新键为追加，既有断言不破）。

- [ ] **Step 5: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 不低于 Task 5 基线 +9。

- [ ] **Step 6: 提交**

```
git add web_host/api/state.py web_host/app.py web_host/api/projects.py web_host/agent/loop.py kernel/documents/informants.py tests/test_web_state_api.py
```
Commit message: `feat(web): 状态修正与确认 API——修正/确认/采纳/方案确认+stale 接线（审计 F03/F04）`

---

### Task 7: 按版本读取与下载——exports by-id 端点 + created_at 口径修正（F13）

**Files:**
- Modify: `web_host/api/exports.py`（抽 `_proposal_payload` helper；新增两个 by-id 端点；L53-54 口径修正）、`web_host/api/projects.py:57`（created_at 口径）
- Test: `tests/test_web_exports_api.py`（末尾追加 4 用例）

**Interfaces:**
- Consumes: `repo.get_proposal_by_id(pid, doc_id) -> dict | None`（Task 3）；save_proposal 落盘记录键 `saved_at`（json_repo.py 现状：无 created_at 键——当前两处 `p.get("created_at")` 恒 None 的 bug）。
- Produces: `GET /projects/{id}/proposals/{doc_id}` → `_proposal_payload` 形状（id/markdown/kind/version/sections，与现有 GET /proposal 同构）；`GET /projects/{id}/proposals/{doc_id}/download` → PlainTextResponse attachment；`_proposal_payload(doc) -> dict` 模块内 helper。

- [ ] **Step 1: 写失败测试**

`tests/test_web_exports_api.py` 末尾追加：

```python
async def test_get_proposal_by_id(client, app):
    repo = app.state.repo
    docs = repo.list_proposals("p1")
    r = await client.get(f"/projects/p1/proposals/{docs[0]['id']}")
    assert r.status_code == 200
    assert "# 标题" in r.json()["markdown"]
    assert r.json()["id"] == docs[0]["id"]


async def test_get_proposal_by_unknown_id_404(client):
    r = await client.get("/projects/p1/proposals/nope")
    assert r.status_code == 404


async def test_download_by_id(client, app):
    repo = app.state.repo
    docs = repo.list_proposals("p1")
    r = await client.get(f"/projects/p1/proposals/{docs[0]['id']}/download")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "# 标题" in r.text


async def test_list_created_at_uses_saved_at(client):
    """口径修正：save_proposal 落盘的是 saved_at，列表不再恒 None。"""
    r = await client.get("/projects/p1/proposals")
    assert r.status_code == 200
    items = r.json()
    assert any(i.get("id", "").startswith("prop_") and i.get("created_at")
               for i in items), f"prop_ 记录的 created_at 应来自 saved_at: {items}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_exports_api.py -k "by_id or created_at_uses" -v`
Expected: 4 FAIL —— 前三 404/路由不存在；第四 created_at 恒 None AssertionError。

- [ ] **Step 3: 写实现**

(a) `web_host/api/exports.py`：router 定义后抽 helper，get_proposal/download_proposal 改用；新增两 by-id 端点；list 的 `created_at` 改 `saved_at`：

```python
def _proposal_payload(doc: dict) -> dict:
    """落盘记录 → 读取端点响应（get_proposal 与 by-id 同构，F13）。"""
    content = doc.get("content", "")
    sections = [ln.lstrip("# ").strip() for ln in content.splitlines()
                if ln.startswith("## ")]
    return {"id": doc.get("id"), "markdown": content,
            "kind": doc.get("kind", "proposal"),
            "version": doc.get("version", "draft"), "sections": sections}


@router.get("/projects/{project_id}/proposals/{doc_id}")
async def get_proposal_by_id(project_id: str, doc_id: str, repo=Depends(get_repo)):
    checked_project_id(project_id, repo)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    doc = repo.get_proposal_by_id(project_id, doc_id)
    if doc is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    return _proposal_payload(doc)


@router.get("/projects/{project_id}/proposals/{doc_id}/download")
async def download_proposal_by_id(project_id: str, doc_id: str, repo=Depends(get_repo)):
    checked_project_id(project_id, repo)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    doc = repo.get_proposal_by_id(project_id, doc_id)
    if doc is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    filename = doc.get("filename", f"{project_id}_{doc_id}.md")
    return PlainTextResponse(
        doc.get("content", ""), media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
```

既有 `get_proposal` 的响应构造换成 `return _proposal_payload(doc)`；`download_proposal` 保持「最新」语义不动。`list_proposals` 末行：

```python
    return [{"id": p["id"], "kind": p.get("kind"), "version": p.get("version"),
             "created_at": p.get("saved_at")} for p in repo.list_proposals(project_id)]
```

（API 契约键名 created_at 不变，前端不动；`legacy_` 记录无 saved_at → None 如实呈现。）

(b) `web_host/api/projects.py` get_project 内同款口径（L57）：`"created_at": p.get("saved_at")`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_exports_api.py tests/test_web_projects_api.py tests/test_web_state_api.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 不低于 Task 6 基线 +4。

- [ ] **Step 6: 提交**

```
git add web_host/api/exports.py web_host/api/projects.py tests/test_web_exports_api.py
```
Commit message: `feat(web): 按版本读取/下载 + created_at 口径修正 saved_at（审计 F13）`

---

### Task 8: 前端 API/类型层——rest 六方法与快照类型扩展（F03/F04/F13 前端接线）

**Files:**
- Modify: `web/src/api/rest.ts`（L2 import 扩展；L36 `ProposalMeta` 后加 `PhaseTransition`；rest 对象加 6 方法）；`web/src/types/models.ts`（`FieldSiteRecord` 加 `id?`；`InformantRecord` 加 `id?`；`ProjectSnapshot` 尾部加两键）
- Test: `web/tests/rest.spec.ts`（新建；`vi.stubGlobal("fetch")` 模式照抄 `web/tests/keys.spec.ts` L34-55）

**Interfaces:**
- Consumes: Task 6 四端点与 Task 7 by-id 两端点的响应形状（`phase_transition` 为 `{from?, to?} | null`；field_site/informant 为记录 dict）。
- Produces（Task 9/10 消费，签名精确）:
  - `export interface PhaseTransition { from?: string; to?: string }`（rest.ts 导出；null = 本次未推进）
  - `rest.updateResearchContext(id: string, patch: Record<string, string | boolean>)` → `{research_context: Partial<ResearchContext>, phase_transition: PhaseTransition | null}`
  - `rest.confirmFieldSite(id: string, siteId: string)` → `{field_site: FieldSiteRecord, phase_transition: PhaseTransition | null}`
  - `rest.adoptInformant(id: string, candidate: Record<string, unknown>)` → `{informant: InformantRecord & {id: string}, phase_transition: PhaseTransition | null}`
  - `rest.confirmProposal(id: string)` → `{confirmation: Record<string, unknown>, proposal_confirmed: boolean, phase_transition: PhaseTransition | null}`
  - `rest.getProposalById(id: string, docId: string)` → `ProposalDoc & {id: string}`；`rest.proposalByIdDownloadUrl(id: string, docId: string) => string`
  - models.ts：`FieldSiteRecord.id?: string` / `InformantRecord.id?: string`（repo 落盘记录带 `id` 键，前缀 `site_`/`inf_`；旧快照无 id → undefined 如实）；`ProjectSnapshot.proposal_confirmed?: boolean` / `proposal_confirmation_stale?: boolean`

- [ ] **Step 1: 写失败测试**

新建 `web/tests/rest.spec.ts`：

```typescript
// web/tests/rest.spec.ts —— rest 六方法请求形状 + 快照新键类型（Task 6/7 端点的前端接线）
import { afterEach, describe, expect, it, vi } from "vitest";
import { rest } from "../src/api/rest";
import type { ProjectSnapshot } from "../src/types/models";

/** stub 全局 fetch，记录 (url, init) 并按序返回假响应。 */
function stubFetch(resps: unknown[] = []) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let i = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const resp = resps[Math.min(i++, resps.length - 1)] ?? {};
    return { ok: true, status: 200, json: async () => resp } as unknown as Response;
  }));
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("rest 状态修正/确认/by-id 方法", () => {
  it("updateResearchContext 发 PUT 与 patch body", async () => {
    const calls = stubFetch([{ research_context: {}, phase_transition: null }]);
    const r = await rest.updateResearchContext("p1", { budget: "5万" });
    expect(calls[0].url).toBe("/projects/p1/research-context");
    expect(calls[0].init?.method).toBe("PUT");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ budget: "5万" });
    expect(r.phase_transition).toBeNull();
  });

  it("confirmFieldSite/confirmProposal 发 POST 到对应端点", async () => {
    const calls = stubFetch([
      { field_site: {}, phase_transition: null },
      { confirmation: {}, proposal_confirmed: true, phase_transition: null },
    ]);
    await rest.confirmFieldSite("p1", "site_1");
    expect(calls[0].url).toBe("/projects/p1/field-sites/site_1/confirm");
    expect(calls[0].init?.method).toBe("POST");
    await rest.confirmProposal("p1");
    expect(calls[1].url).toBe("/projects/p1/proposal/confirm");
    expect(calls[1].init?.method).toBe("POST");
  });

  it("adoptInformant 以 {candidate} 包装 POST", async () => {
    const calls = stubFetch([{ informant: { id: "inf_1" }, phase_transition: null }]);
    const r = await rest.adoptInformant("p1", { type: "宗族长老" });
    expect(calls[0].url).toBe("/projects/p1/informants");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ candidate: { type: "宗族长老" } });
    expect(r.informant.id).toBe("inf_1");
  });

  it("getProposalById 与下载 URL 指向 by-id 端点", async () => {
    const calls = stubFetch([{ id: "prop_1", markdown: "# x" }]);
    const r = await rest.getProposalById("p1", "prop_1");
    expect(calls[0].url).toBe("/projects/p1/proposals/prop_1");
    expect(r.id).toBe("prop_1");
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1")).toBe(
      "/projects/p1/proposals/prop_1/download");
  });
});

describe("types: 快照确认状态与记录 id", () => {
  it("ProjectSnapshot 携带 proposal_confirmed/stale 与记录 id（可选）", () => {
    const s: ProjectSnapshot = {
      project_id: "p1", phase: "finished", research_context: {},
      field_sites: [{ id: "site_1", name: "大理", confirmed: true, profile: null }],
      informants: [{ id: "inf_1", code: "INF-01", type: "长老",
                     relation: "", contact: "", notes: "" }],
      proposals: [], phase_history: [],
      proposal_confirmed: true, proposal_confirmation_stale: false,
    };
    expect(s.proposal_confirmed).toBe(true);
    expect(s.field_sites[0].id).toBe("site_1");
    expect(s.informants[0].id).toBe("inf_1");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web; [Console]::OutputEncoding = [Text.Encoding]::UTF8; npm test -- rest.spec.ts`
Expected: FAIL —— `rest.updateResearchContext is not a function`；类型用例因 `id`/`proposal_confirmed` 不在接口上报属性不存在。

- [ ] **Step 3: 写实现**

(a) `web/src/api/rest.ts`——L2 import 扩展为：

```typescript
import type {
  FieldSiteRecord, InformantRecord, ProjectSnapshot, ProjectSummary,
  ProposalDoc, ResearchContext,
} from "../types/models";
```

L36 `ProposalMeta` 接口后加：

```typescript
/** 状态修正/确认端点共同返回的阶段推进结果；null = 本次未发生推进。 */
export interface PhaseTransition {
  from?: string;
  to?: string;
}
```

rest 对象尾部（`proposalDownloadUrl` 之后）加 6 方法：

```typescript
  // ---- 状态修正与确认（web_host/api/state.py，审计 F03/F04 修正入口）----
  updateResearchContext: (id: string, patch: Record<string, string | boolean>) =>
    req<{ research_context: Partial<ResearchContext>; phase_transition: PhaseTransition | null }>(
      `/projects/${id}/research-context`, { method: "PUT", body: JSON.stringify(patch) }),
  confirmFieldSite: (id: string, siteId: string) =>
    req<{ field_site: FieldSiteRecord; phase_transition: PhaseTransition | null }>(
      `/projects/${id}/field-sites/${siteId}/confirm`, { method: "POST" }),
  adoptInformant: (id: string, candidate: Record<string, unknown>) =>
    req<{ informant: InformantRecord & { id: string }; phase_transition: PhaseTransition | null }>(
      `/projects/${id}/informants`, { method: "POST", body: JSON.stringify({ candidate }) }),
  confirmProposal: (id: string) =>
    req<{ confirmation: Record<string, unknown>; proposal_confirmed: boolean;
          phase_transition: PhaseTransition | null }>(
      `/projects/${id}/proposal/confirm`, { method: "POST" }),
  // ---- 按版本读取/下载（web_host/api/exports.py by-id 端点，审计 F13）----
  getProposalById: (id: string, docId: string) =>
    req<ProposalDoc & { id: string }>(`/projects/${id}/proposals/${docId}`),
  proposalByIdDownloadUrl: (id: string, docId: string) =>
    `/projects/${id}/proposals/${docId}/download`,
```

(b) `web/src/types/models.ts`——`FieldSiteRecord`/`InformantRecord` 各在首字段前加 `id?: string;`（注释补：repo 记录键，`site_`/`inf_` 前缀，旧快照无此键→undefined）；`ProjectSnapshot` 尾部（`updated_at?: string;` 后）加：

```typescript
  /** 方案确认状态（web_host/api/projects.py get_project 新增两键）。 */
  proposal_confirmed?: boolean;
  /** true = 确认后研究信息已变更，需重新确认。 */
  proposal_confirmation_stale?: boolean;
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web; npm test -- rest.spec.ts`
Expected: 5 passed。

- [ ] **Step 5: 全量回归**

Run: `cd web; npm test; npm run build`
Expected: 前端不低于 Task 7 后基线 +5；tsc 无错（新键全 optional，不破坏既有赋值与断言）。

- [ ] **Step 6: 提交**

```
git add web/src/api/rest.ts web/src/types/models.ts web/tests/rest.spec.ts
```
Commit message: `feat(web): rest 六方法与快照类型扩展——修正/确认/采纳/by-id（审计 F03/F04/F13）`

---

### Task 9: 右栏状态修正入口——田野点确认、报道人采纳、研究信息编辑（F03/F04 前端）

**Files:**
- Modify: `web/src/components/cards/InformantCard.tsx`（可选 prop `cardActions`，小卡 dl 后注入操作区，中栏不传时渲染逐字节不变）；`web/src/components/right/InformantsPanel.tsx`（候选节注入「采纳登记」）；`web/src/components/right/FieldSitesPanel.tsx`（SiteView 加 `id`；SiteBlock 加 `onConfirm`；待确认 chip 换确认按钮）；`web/src/components/right/ProgressPanel.tsx`（底部加「研究信息」编辑区 `ResearchContextEditor`）
- Test: `web/tests/rightPanels.spec.tsx`（新建；zustand store 直接 `setState` + `vi.stubGlobal("fetch")`，组件渲染模式照抄 toolCards.spec.tsx）

**Interfaces:**
- Consumes: `rest.updateResearchContext/confirmFieldSite/adoptInformant/getProject`（Task 8）；`useProjectStore.getState().loadSnapshot`（既有）；`snapshot.field_sites[].id`（Task 8 类型扩展）。
- Produces（Task 10 消费模式同源）:
  - `InformantCard` 可选 prop：`cardActions?: (candidate: Record<string, unknown>, index: number) => ReactNode`
  - `FieldSitesPanel`：`SiteView` 加 `id: string`（记录项取 `strOf(r.id)`，live 项 `""`）；`SiteBlock` 加 `onConfirm?: (site: SiteView) => void` / `confirming?: string | null`
  - `ProgressPanel`：内部组件 `ResearchContextEditor({ snapshot }: { snapshot: ProjectSnapshot | null })`（10 文本槽位 + NA checkbox + naReason + 保存；只提交非空字段）

**设计红线**：确认/采纳/修正都是**用户显式动作**（REST），与 LLM 回合内规则抽取互补；成功后统一 `rest.getProject` → `loadSnapshot` 刷新（右栏为快照投影，不局部改 state）；失败把 `ApiError.message`（服务端中文 detail）原样呈现，不吞错。

- [ ] **Step 1: 写失败测试**

新建 `web/tests/rightPanels.spec.tsx`：

```tsx
// web/tests/rightPanels.spec.tsx —— 右栏确认/采纳/研究信息编辑（Task 9）
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { InformantCard } from "../src/components/cards/InformantCard";
import { InformantsPanel } from "../src/components/right/InformantsPanel";
import { FieldSitesPanel } from "../src/components/right/FieldSitesPanel";
import { ProgressPanel } from "../src/components/right/ProgressPanel";
import { useProjectStore } from "../src/stores/project";
import { useToolsStore } from "../src/stores/tools";
import type { ProjectSnapshot } from "../src/types/models";

function stubFetch(resps: unknown[] = []) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let i = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const resp = resps[Math.min(i++, resps.length - 1)] ?? {};
    return { ok: true, status: 200, json: async () => resp } as unknown as Response;
  }));
  return calls;
}

const SNAP: ProjectSnapshot = {
  project_id: "p1", phase: "informants", research_context: { budget: "2万" },
  field_sites: [{ id: "site_1", name: "大理", confirmed: false,
                  profile: { location: "大理" } }],
  informants: [], proposals: [], phase_history: [],
};

beforeEach(() => {
  useProjectStore.getState().reset();
  useProjectStore.getState().loadSnapshot(SNAP);
  useToolsStore.setState({ byCallId: {}, order: [] });
});
afterEach(() => vi.unstubAllGlobals());

describe("InformantCard cardActions 注入", () => {
  test("传入 cardActions 时每张候选小卡渲染操作区；不传不渲染", () => {
    const { rerender } = render(
      <InformantCard data={[{ type: "宗族长老" }]}
        cardActions={(_c, i) => <button type="button">{`采纳 ${i}`}</button>} />,
    );
    expect(screen.getByText("采纳 0")).toBeInTheDocument();
    rerender(<InformantCard data={[{ type: "宗族长老" }]} />);
    expect(screen.queryByText("采纳 0")).not.toBeInTheDocument();
  });
});

describe("InformantsPanel 采纳登记", () => {
  test("点采纳 → POST /informants + 刷新快照", async () => {
    useToolsStore.setState({
      byCallId: { c1: { tool: "suggest_informants", status: "done", args: {},
                        data: [{ type: "宗族长老" }], degraded: null } },
      order: ["c1"],
    });
    const calls = stubFetch([
      { informant: { id: "inf_1" }, phase_transition: null },
      { ...SNAP, informants: [{ id: "inf_1", code: "INF-01", type: "宗族长老",
                                relation: "", contact: "", notes: "" }] },
    ]);
    render(<InformantsPanel />);
    fireEvent.click(screen.getByText("采纳登记"));
    await waitFor(() => expect(calls[0].url).toBe("/projects/p1/informants"));
    expect(JSON.parse(String(calls[0].init?.body))).toEqual(
      { candidate: { type: "宗族长老" } });
    await waitFor(() =>
      expect(useProjectStore.getState().snapshot?.informants).toHaveLength(1));
  });
});

describe("FieldSitesPanel 确认", () => {
  test("未确认且带 id 的记录显示确认按钮；点击 POST 后刷新", async () => {
    const calls = stubFetch([
      { field_site: { id: "site_1", confirmed: true }, phase_transition: null },
      { ...SNAP, field_sites: [{ ...SNAP.field_sites[0], confirmed: true }] },
    ]);
    render(<FieldSitesPanel />);
    fireEvent.click(screen.getByText("确认"));
    await waitFor(() =>
      expect(calls[0].url).toBe("/projects/p1/field-sites/site_1/confirm"));
    await waitFor(() =>
      expect(useProjectStore.getState().snapshot?.field_sites[0].confirmed).toBe(true));
  });

  test("本回合 live 画像（无记录 id）不显示确认按钮", () => {
    useProjectStore.getState().loadSnapshot({ ...SNAP, field_sites: [] });
    useToolsStore.setState({
      byCallId: { c2: { tool: "profile_field_site", status: "done",
                        args: { location: "喜洲" }, data: { location: "喜洲" },
                        degraded: null } },
      order: ["c2"],
    });
    render(<FieldSitesPanel />);
    expect(screen.getByText("待确认")).toBeInTheDocument(); // live-only 只读 chip
    expect(screen.queryByText("确认")).not.toBeInTheDocument();
  });
});

describe("ProgressPanel 研究信息编辑", () => {
  test("改预算并保存 → PUT research-context 只带非空字段", async () => {
    const calls = stubFetch([
      { research_context: { budget: "5万" }, phase_transition: null },
      { ...SNAP, research_context: { budget: "5万" } },
    ]);
    render(<ProgressPanel />);
    fireEvent.change(screen.getByLabelText("预算"), { target: { value: "5万" } });
    fireEvent.click(screen.getByText("保存修正"));
    await waitFor(() =>
      expect(calls[0].url).toBe("/projects/p1/research-context"));
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ budget: "5万" });
    await waitFor(() =>
      expect(useProjectStore.getState().snapshot?.research_context.budget).toBe("5万"));
  });

  test("未改任何字段点保存 → 不发请求并提示", async () => {
    useProjectStore.getState().loadSnapshot({ ...SNAP, research_context: {} });
    const calls = stubFetch([]);
    render(<ProgressPanel />);
    fireEvent.click(screen.getByText("保存修正"));
    await waitFor(() =>
      expect(screen.getByText(/未填写任何要修正的字段/)).toBeInTheDocument());
    expect(calls).toHaveLength(0);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web; [Console]::OutputEncoding = [Text.Encoding]::UTF8; npm test -- rightPanels.spec.tsx`
Expected: FAIL —— ① InformantCard 无 cardActions prop（TS 报未知属性/不渲染「采纳 0」）；② 面板无「采纳登记/确认/保存修正」元素，getByText 抛 NotFound。

- [ ] **Step 3: 写实现**

(a) `web/src/components/cards/InformantCard.tsx`——L1 加 `import type { ReactNode } from "react";`；组件签名改为：

```tsx
/** cardActions：右栏面板注入的每卡操作区（中栏时间线不传，渲染逐字节不变）。 */
interface InformantCardProps extends ToolCardProps {
  cardActions?: (candidate: Record<string, unknown>, index: number) => ReactNode;
}

export function InformantCard({ data, degraded, cardActions }: InformantCardProps) {
```

candidates.map 内 `</dl>` 之后、小卡收尾 `</div>` 之前插：

```tsx
                {cardActions && (
                  <div className="mt-2 flex items-center gap-2">{cardActions(c, i)}</div>
                )}
```

(b) `web/src/components/right/InformantsPanel.tsx`——文件头加 `import { useState } from "react";` 与 `import { rest } from "../../api/rest";`；组件内（candidateCall 查找后）加：

```tsx
  const [adopting, setAdopting] = useState<number | null>(null);
  const [adoptError, setAdoptError] = useState<string | null>(null);

  // 采纳登记：POST /informants（候选原样上行）→ 刷新快照（右栏为快照投影，不局部改）
  const adopt = async (candidate: Record<string, unknown>, i: number) => {
    const pid = snapshot?.project_id;
    if (!pid || adopting !== null) return;
    setAdopting(i);
    setAdoptError(null);
    try {
      await rest.adoptInformant(pid, candidate);
      useProjectStore.getState().loadSnapshot(await rest.getProject(pid));
    } catch (e) {
      setAdoptError(e instanceof Error ? e.message : String(e));
    } finally {
      setAdopting(null);
    }
  };
```

候选节 `<InformantCard .../>` 加 `cardActions`，候选节标签后加 adoptError 呈现：

```tsx
          <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">
            候选 · 未登记
            {adoptError && <span className="text-ochre ml-2 normal-case">{adoptError}</span>}
          </div>
          {/* 同源复用：与中栏时间线同一组件 + 同一 tool_completed.data；cardActions 注入采纳 */}
          <InformantCard
            data={candidateCall.data === undefined ? null : candidateCall.data}
            degraded={candidateCall.degraded ?? null}
            cardActions={(candidate, i) => (
              <button
                type="button"
                disabled={adopting !== null}
                onClick={() => adopt(candidate, i)}
                className="text-[11.5px] text-cinnabar hairline rounded-md px-2.5 py-1 hover:bg-cardsunk disabled:opacity-50"
              >
                {adopting === i ? "登记中…" : "采纳登记"}
              </button>
            )}
          />
```

(c) `web/src/components/right/FieldSitesPanel.tsx`——L1 合并 react import（`import { useState, type ReactNode } from "react";`），另加 `import { rest } from "../../api/rest";`；`SiteView` 首字段前加：

```tsx
  /** repo 记录键（site_ 前缀）；live-only 项为 ""（快照尚未落盘，不可确认） */
  id: string;
```

records.map 内补 `id: strOf(r.id),`；live 追加项补 `id: ""`。`SiteBlock` 签名与 chip 区待确认分支改为：

```tsx
function SiteBlock({ site, onConfirm, confirming }: {
  site: SiteView;
  onConfirm?: (site: SiteView) => void;
  confirming?: string | null;
}) {
```

```tsx
          {site.confirmed ? (
            <Chip tone="bamboo">
              <IconCheck className="w-3 h-3" />
              已确认
            </Chip>
          ) : site.liveOnly || !site.id ? (
            <Chip tone="neutral">待确认</Chip>
          ) : (
            <button
              type="button"
              disabled={confirming !== null}
              onClick={() => onConfirm?.(site)}
              className="text-[11.5px] px-2.5 py-0.5 rounded-md hairline text-cinnabar hover:bg-cardsunk disabled:opacity-50"
            >
              {confirming === site.id ? "确认中…" : "确认"}
            </button>
          )}
```

`FieldSitesPanel` 组件内（sites 派生前）加确认动作，渲染处传回调：

```tsx
  const [confirming, setConfirming] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  const confirmSite = async (site: SiteView) => {
    const pid = snapshot?.project_id;
    if (!pid || !site.id || confirming !== null) return;
    setConfirming(site.id);
    setConfirmError(null);
    try {
      await rest.confirmFieldSite(pid, site.id);
      useProjectStore.getState().loadSnapshot(await rest.getProject(pid));
    } catch (e) {
      setConfirmError(e instanceof Error ? e.message : String(e));
    } finally {
      setConfirming(null);
    }
  };
```

```tsx
      <div className="font-serif text-[15px] font-semibold mb-3">{`田野点 · ${sites.length}`}</div>
      {confirmError && <div className="text-[11px] text-ochre mb-2">{confirmError}</div>}
      {sites.map((site, i) => (
        <SiteBlock key={`${site.name}-${i}`} site={site}
                   onConfirm={confirmSite} confirming={confirming} />
      ))}
```

(d) `web/src/components/right/ProgressPanel.tsx`——import 补 `useEffect/useState`（react）、`rest`（api）、`strOf`（cards/data）、`ProjectSnapshot`（types）；文件底部（组件外）加：

```tsx
/** 可编辑研究信息槽位（= ALLOWED_CONTEXT_FIELDS 的 10 个文本键；NA 布尔单独处理）。 */
const EDIT_FIELDS: { key: string; label: string }[] = [
  { key: "discipline", label: "学科背景" },
  { key: "topic", label: "研究主题" },
  { key: "site_status", label: "身份/在场方式" },
  { key: "scale", label: "规模" },
  { key: "budget", label: "预算" },
  { key: "time_window", label: "时间窗" },
  { key: "departure_city", label: "出发城市" },
  { key: "target_location", label: "目标地点" },
  { key: "travel_date", label: "出行日期" },
  { key: "purpose", label: "输出用途" },
];

/** 研究信息编辑区：用户显式修正入口（审计 F03 修正入口右栏编辑；与回合内规则抽取互补）。 */
function ResearchContextEditor({ snapshot }: { snapshot: ProjectSnapshot | null }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [na, setNa] = useState(false);
  const [naReason, setNaReason] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedHint, setSavedHint] = useState<string | null>(null);

  // 快照异步到达/外部刷新时同步草稿；用户编辑中（dirty）不覆盖，保存成功后复位
  useEffect(() => {
    if (dirty) return;
    const ctx = (snapshot?.research_context ?? {}) as Record<string, unknown>;
    const next: Record<string, string> = {};
    for (const f of EDIT_FIELDS) next[f.key] = strOf(ctx[f.key]);
    setDraft(next);
    setNa(ctx.interviews_not_applicable === true);
    setNaReason(strOf(ctx.interviews_na_reason));
  }, [snapshot, dirty]);

  const save = async () => {
    const pid = snapshot?.project_id;
    if (!pid || saving) return;
    const patch: Record<string, string | boolean> = {};
    for (const f of EDIT_FIELDS) {
      const v = (draft[f.key] ?? "").trim();
      if (v !== "") patch[f.key] = v; // 空字段不上行（PUT 空白字段无意义且会被后端清空语义误读）
    }
    if (na) {
      patch.interviews_not_applicable = true;
      if (naReason.trim() !== "") patch.interviews_na_reason = naReason.trim();
    }
    if (Object.keys(patch).length === 0) {
      setError("未填写任何要修正的字段（空字段不覆盖已有值）");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await rest.updateResearchContext(pid, patch);
      const snap2 = await rest.getProject(pid);
      useProjectStore.getState().loadSnapshot(snap2);
      setDirty(false);
      setSavedHint(snap2.proposal_confirmation_stale
        ? "已保存；此前的方案确认已因研究信息变更失效"
        : "已保存");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="hairline rounded-lg bg-card p-3">
      <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">研究信息（可修正）</div>
      {snapshot?.proposal_confirmation_stale === true && (
        <div className="text-[11px] text-ochre mb-2">
          方案确认已因研究信息变更失效，请在「方案」页重新确认。
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        {EDIT_FIELDS.map((f) => (
          <label key={f.key} className="block min-w-0">
            <span className="text-[10px] text-inkfaint">{f.label}</span>
            <input
              aria-label={f.label}
              value={draft[f.key] ?? ""}
              onChange={(e) => {
                setDraft({ ...draft, [f.key]: e.target.value });
                setDirty(true);
              }}
              className="w-full mt-0.5 text-[12px] bg-cardsunk hairline rounded-md px-2 py-1 outline-none focus:border-cinnabar"
            />
          </label>
        ))}
      </div>
      <label className="flex items-center gap-2 mt-2 text-[12px] text-inksoft">
        <input
          type="checkbox"
          checked={na}
          onChange={(e) => { setNa(e.target.checked); setDirty(true); }}
        />
        本研究不适用访谈对象（非访谈型研究）
      </label>
      {na && (
        <input
          aria-label="不适用原因"
          placeholder="不适用原因（可选，呈现于访谈对象面板）"
          value={naReason}
          onChange={(e) => { setNaReason(e.target.value); setDirty(true); }}
          className="w-full mt-1.5 text-[12px] bg-cardsunk hairline rounded-md px-2 py-1 outline-none focus:border-cinnabar"
        />
      )}
      <div className="flex items-center gap-2 mt-2">
        <button
          type="button"
          disabled={saving}
          onClick={save}
          className="text-[11.5px] text-cinnabar hairline rounded-md px-2.5 py-1 hover:bg-cardsunk disabled:opacity-50"
        >
          {saving ? "保存中…" : "保存修正"}
        </button>
        {savedHint && !error && <span className="text-[11px] text-bamboo">{savedHint}</span>}
        {error && <span className="text-[11px] text-ochre">{error}</span>}
      </div>
    </div>
  );
}
```

主组件 return 末尾（阶段流转史 `</ul>` / 空态 div 之后、收尾 `</div>` 之前）加：

```tsx
      <div className="rule my-4" />
      <ResearchContextEditor snapshot={snapshot} />
```

（主组件已取 `const snapshot = useProjectStore((s) => s.snapshot);`，直接传入。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web; npm test -- rightPanels.spec.tsx`
Expected: 6 passed。

- [ ] **Step 5: 全量回归**

Run: `cd web; npm test; npm run build`
Expected: 前端不低于 Task 8 基线 +6；tsc 无错（中栏 InformantCard 未传 cardActions，既有 toolCards.spec.tsx 不破）。

- [ ] **Step 6: 提交**

```
git add web/src/components/cards/InformantCard.tsx web/src/components/right/InformantsPanel.tsx web/src/components/right/FieldSitesPanel.tsx web/src/components/right/ProgressPanel.tsx web/tests/rightPanels.spec.tsx
```
Commit message: `feat(web): 右栏状态修正入口——田野点确认/报道人采纳/研究信息编辑（审计 F03/F04）`

---

### Task 10: 提案阅读页全功能——版本/kind 按 id 加载 + 方案确认入口（F13/F04 前端）

**Files:**
- `web/src/routes/ProposalView.tsx`（重构）
- `web/tests/proposalView.spec.tsx`（新建）
- `web/tests/routes.spec.tsx`（适配下载 URL 断言）

**Interfaces:**
- Consumes: `rest.getProposalById(projectId, docId)` → `GET /projects/{id}/proposals/{doc_id}`（Task 8）；`rest.proposalByIdDownloadUrl(projectId, docId)`（Task 8）；`rest.confirmProposal(projectId)` → `POST /projects/{id}/proposal/confirm`（Task 6，返回 `{confirmation, proposal_confirmed, phase_transition}`）；`rest.getProject(projectId)` 快照含 `proposal_confirmed` / `proposal_confirmation_stale`（Task 5）
- Produces: 提案页交互闭环——版本 chip 与 kind 切换按钮按 id 加载对应正文（替换现状「仅切本地 state + mismatch 限制提示卡」），新增「确认方案」按钮（完成后回显阶段推进）与 stale 失效提示

**背景**：现状 `toggleKind` / `VersionSwitch onSelect` 只改本地 state（`selectedId` / `selectedKind`），正文恒为最新文档，靠 mismatch 限制卡提示「仅返回最新保存的文档」（审计 F13）。本任务把选中切换接到 Task 7/8 的 by-id 端点上，删掉 `mismatch` / `backToLatest` 整套逻辑；同时把 Task 5/6 的确认与 stale 状态露到 UI（审计 F04 前端半边）。

- [ ] **Step 1: 写失败测试**

新建 `web/tests/proposalView.spec.tsx`（真实 rest + stubGlobal fetch 按 URL 分发，断言真实 URL 与 method）：

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProposalView } from "../src/routes/ProposalView";

const LATEST = {
  id: "prop_2",
  kind: "proposal",
  version: "final",
  created_at: "2026-10-16T10:00:00",
  markdown: "# 最新版正文\n\n段落。",
  sections: [{ id: "ch-1", title: "最新版正文", level: 2 }],
};
const V1 = {
  id: "prop_1",
  kind: "proposal",
  version: "draft",
  created_at: "2026-10-15T10:00:00",
  markdown: "# 初版正文\n\n段落。",
  sections: [{ id: "ch-1", title: "初版正文", level: 2 }],
};
const GUIDE = {
  id: "guide_1",
  kind: "interview_guide",
  version: "draft",
  created_at: "2026-10-16T11:00:00",
  markdown: "# 提纲正文\n\n段落。",
  sections: [{ id: "ch-1", title: "提纲正文", level: 2 }],
};
const VERSIONS = [V1, LATEST, GUIDE];
const SNAP = {
  id: "p1",
  phase: "proposal_generation",
  research_context: { topic: "宗族变迁" },
  field_sites: [],
  informants: [],
};

function json(payload: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => String(payload),
  } as Response;
}

/** 按 URL 分发的 fetch stub；返回调用记录（url + init）。 */
function stubFetch(snap: object = SNAP) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/projects/p1") return json(snap);
      if (url === "/projects/p1/proposals") return json(VERSIONS);
      if (url === "/projects/p1/proposals/prop_1") return json(V1);
      if (url === "/projects/p1/proposals/guide_1") return json(GUIDE);
      if (url === "/projects/p1/proposal") return json(LATEST);
      if (url === "/projects/p1/proposal/confirm") {
        return json({
          confirmation: {},
          proposal_confirmed: true,
          phase_transition: { from: "proposal_generation", to: "finished" },
        });
      }
      return json({});
    }),
  );
  return calls;
}

function mount() {
  return render(
    <MemoryRouter initialEntries={["/p/p1/proposal"]}>
      <Routes>
        <Route path="/p/:id/proposal" element={<ProposalView />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("ProposalView 版本/kind 加载与方案确认", () => {
  it("初载渲染最新版正文与 final chip（回归保护，现状即绿）", async () => {
    const calls = stubFetch();
    mount();
    expect(await screen.findByText("最新版正文")).toBeTruthy();
    expect(screen.getByText("final")).toBeTruthy();
    const urls = calls.map((c) => c.url);
    expect(urls).toContain("/projects/p1/proposal");
    expect(urls).toContain("/projects/p1/proposals");
  });

  it("点版本 chip 后按 id 加载对应正文（F13）", async () => {
    const calls = stubFetch();
    mount();
    await screen.findByText("最新版正文");
    // VERSIONS 顺序 [V1, LATEST, GUIDE]：DOM 中第一枚「draft v1」即 prop_1
    fireEvent.click(screen.getAllByText("draft v1")[0]);
    expect(await screen.findByText("初版正文")).toBeTruthy();
    expect(calls.some((c) => c.url === "/projects/p1/proposals/prop_1")).toBe(true);
  });

  it("点 kind 切换按钮加载访谈提纲正文（F13）", async () => {
    stubFetch();
    mount();
    await screen.findByText("最新版正文");
    fireEvent.click(screen.getByRole("button", { name: /访谈提纲/ }));
    expect(await screen.findByText("提纲正文")).toBeTruthy();
  });

  it("点确认方案——POST confirm、回读快照、提示阶段推进（F04）", async () => {
    const calls = stubFetch();
    mount();
    await screen.findByText("最新版正文");
    fireEvent.click(screen.getByRole("button", { name: "确认方案" }));
    await waitFor(() => screen.getByText(/阶段推进至 finished/));
    const confirms = calls.filter((c) => c.url === "/projects/p1/proposal/confirm");
    expect(confirms).toHaveLength(1);
    expect((confirms[0].init as RequestInit | undefined)?.method).toBe("POST");
    // confirm 后需回读项目快照刷新题头/阶段/确认状态
    expect(calls.filter((c) => c.url === "/projects/p1").length).toBeGreaterThanOrEqual(2);
  });

  it("stale 快照——提示重新确认且按钮文案变化（F04）", async () => {
    stubFetch({ ...SNAP, proposal_confirmed: true, proposal_confirmation_stale: true });
    mount();
    expect(await screen.findByText(/请重新确认/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新确认方案" })).toBeTruthy();
  });
});
```

- [ ] **Step 2: 跑测试确认失败（TDD 红灯）**

Run: `cd web; npm test -- proposalView.spec.tsx`
Expected: 用例 1 通过（现状初载即最新文档 + final chip）；用例 2/3 失败（现状切换只改本地 state，正文不换、无 by-id 请求）；用例 4 失败（无「确认方案」按钮）；用例 5 失败（无 stale 提示）。1 passed / 4 failed。

- [ ] **Step 3: 实现（八处改动，均含精确锚点）**

以下行号均指重构前的 `web/src/routes/ProposalView.tsx`（248 行版）。

**(a) 顶部注释（L22-23）**：将「API 限制（exports.py）……不伪造内容（红线②同源精神）」两行替换为：

```tsx
 * 数据源（审计 F13 修复）：选中版本 / 经 kind 切换选中条目后，改走 by-id 端点
 *   GET /projects/{id}/proposals/{doc_id} 加载对应正文；下载同理走 by-id 下载端点。
 * 确认入口（审计 F04）：POST /projects/{id}/proposal/confirm + proposal_confirmed /
 *   proposal_confirmation_stale 快照字段驱动「确认方案 / 重新确认方案」与失效提示。
```

**(b) state 区（L46-49）**：删除 `selectedKind` 一行；在 `activeIndex` 之后、`bodyRef` 之前追加：

```tsx
  const [confirmed, setConfirmed] = useState(false);
  const [stale, setStale] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmHint, setConfirmHint] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
```

**(c) 初载 useEffect（L51-87）整块替换为三个 effect**：

```tsx
  // 初载正文：最新文档（loadDoc 无参 = GET /projects/{id}/proposal）
  useEffect(() => {
    void loadDoc();
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    rest
      .listProposals(projectId)
      .then((list) => {
        if (alive) setVersions(list);
      })
      .catch(() => {
        // 版本列表失败不阻塞正文阅读：留空并由 doc 兜底派生单条
        if (alive) setVersions([]);
      });
    return () => {
      alive = false;
    };
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    rest
      .getProject(projectId)
      .then((snap) => {
        if (!alive) return;
        setProjectName(snap.research_context?.topic || "");
        setPhase(snap.phase);
        setConfirmed(!!snap.proposal_confirmed);
        setStale(!!snap.proposal_confirmation_stale);
      })
      .catch(() => {
        // 项目名仅用于题头展示，失败降级为项目 id（不伪造）
      });
    return () => {
      alive = false;
    };
  }, [projectId]);
```

（`loadDoc` 为 function 声明，提升到组件体顶部，effect 引用无碍。）

**(d) IntersectionObserver 依赖（L110）**：`}, [doc, selectedId, selectedKind]);` → `}, [doc, selectedId]);`（selectedKind 已删；正文换载后 doc 引用变化即重新观察）。

**(e) L119-158 整块替换**（原 download / otherKind+toggleKind / backToLatest / versionList+mismatch）：

```tsx
  // 下载 .md：/projects 前缀受 F02 令牌保护，顶层锚点导航带不了自定义头
  // （启用 App Token 必 401）→ fetch + authHeaders() 取 blob 走临时链下载。
  // F13：按当前选中文档的 id 走 by-id 下载端点（历史版本也能下载）。
  async function download() {
    if (!doc) return;
    setDlError(null);
    try {
      const resp = await fetch(rest.proposalByIdDownloadUrl(projectId, doc.id), {
        headers: authHeaders(),
      });
      if (!resp.ok) throw new ApiError(resp.status, "下载失败");
      const url = URL.createObjectURL(await resp.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: "" });
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setDlError(err instanceof ApiError ? `下载失败（HTTP ${err.status}）` : `下载失败：${String(err)}`);
    }
  }

  /** 加载文档正文：无参 = 最新（GET /proposal）；带 id = 指定版本/kind（by-id，F13）。 */
  async function loadDoc(docId?: string) {
    if (!projectId) return;
    try {
      const d = docId
        ? await rest.getProposalById(projectId, docId)
        : await rest.getProposal(projectId);
      setDoc(d);
      setSelectedId(d.id);
      setError(null);
    } catch (err: unknown) {
      setError(`提案加载失败：${String(err)}`);
    }
  }

  const otherKind = doc?.kind === "interview_guide" ? "proposal" : "interview_guide";

  // kind 切换（F13）：从版本列表选该 kind 最新一条，按 id 加载正文；
  // 列表为空（接口失败兜底）或该 kind 暂无文档时如实提示，不伪造内容。
  function toggleKind() {
    const pool = versions.length > 0 ? versions : versionList;
    const sameKind = pool.filter((v) => v.kind === otherKind);
    const target = sameKind[sameKind.length - 1];
    if (!target) {
      setError(`暂无${kindLabel(otherKind)}文档`);
      return;
    }
    void loadDoc(target.id);
  }

  // 确认方案（F04）：POST confirm → 回读快照刷新题头/阶段/确认与 stale 状态。
  // 阶段推进提示来自后端确定性推进结果（phase_transition.to）。
  async function doConfirm() {
    if (!projectId) return;
    setConfirming(true);
    setConfirmError(null);
    setConfirmHint(null);
    try {
      const res = await rest.confirmProposal(projectId);
      const snap = await rest.getProject(projectId);
      setProjectName(snap.research_context?.topic || "");
      setPhase(snap.phase);
      setConfirmed(!!snap.proposal_confirmed);
      setStale(!!snap.proposal_confirmation_stale);
      const to = res.phase_transition?.to;
      setConfirmHint(to ? `方案已确认，阶段推进至 ${to}` : "方案已确认");
    } catch (err: unknown) {
      setConfirmError(
        err instanceof ApiError ? `确认失败（HTTP ${err.status}）` : `确认失败：${String(err)}`,
      );
    } finally {
      setConfirming(false);
    }
  }

  // 版本列表失败/为空时用当前文档兜底派生单条（保证 VersionSwitch 有当前项高亮）
  const versionList: ProposalMeta[] =
    versions.length > 0
      ? versions
      : doc
        ? [{ id: doc.id, kind: doc.kind, version: doc.version }]
        : [];
```

（`backToLatest` 与 `mismatch` 定义制除——mismatch 卡与「回到最新版本」按钮随 (g) 一并删掉。）

**(f) 左栏 JSX 四处**：
1. 版本区（原 L183）：`onSelect={setSelectedId}` → `onSelect={(vid) => void loadDoc(vid)}`；并在版本区容器尾部（「暂无版本记录」分支后、`</div>` 前）追加切换失败小字（双通道设计：切换失败不丢当前正文，正文区仍显示 doc）：

```tsx
            {doc && error && (
              <div className="text-[11px] text-cinnabar mt-2 leading-snug">{error}</div>
            )}
```

2. ChapterNav（原 L189）：`sections={doc && !mismatch ? doc.sections : []}` → `sections={doc ? doc.sections : []}`
3. 底部按钮区：kind 切换按钮（文案 `{kindLabel(otherKind)}（kind 切换）` 保持不变）之后、`</div>` 前追加：

```tsx
            {confirmed && !stale && (
              <div className="text-[11px] text-bamboo leading-snug">方案已确认</div>
            )}
            {stale && (
              <div className="text-[11px] text-ochre leading-snug">
                研究信息已变更，此前的方案确认已失效，请重新确认
              </div>
            )}
            <Button
              variant="outline"
              className="w-full"
              disabled={!doc || confirming}
              onClick={doConfirm}
            >
              {confirming ? "确认中…" : confirmed || stale ? "重新确认方案" : "确认方案"}
            </Button>
            {confirmHint && (
              <div className="text-[11px] text-bamboo leading-snug">{confirmHint}</div>
            )}
            {confirmError && (
              <div className="text-[11px] text-cinnabar leading-snug">{confirmError}</div>
            )}
```

**(g) 右栏正文（原 L213-242）三分支简化为两分支**——删除 mismatch 中间分支（含「回到最新版本」按钮卡），保留错误卡与加载中：

```tsx
          {doc ? (
            <ProposalViewer markdown={doc.markdown} kind={doc.kind} version={doc.version} />
          ) : error ? (
            （原错误卡 + 「返回工作台」两段保持不变）
          ) : (
            <div className="text-center text-inkfaint text-[13px] py-16">提案加载中…</div>
          )}
```

**(h) `web/tests/routes.spec.tsx` 适配（下载用例走 by-id URL）**：
1. beforeEach 中 `vi.mocked(rest.rest.proposalDownloadUrl).mockImplementation(...)` 行后追加：

```ts
    vi.mocked(rest.rest.proposalByIdDownloadUrl).mockImplementation(
      (id: string, docId: string) => `/projects/${id}/proposals/${docId}/download`,
    );
```

2. 成功用例断言：`expect(calls[0][0]).toBe("/projects/p9/proposal/download")` → `.toBe("/projects/p9/proposals/doc1/download")`

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web; npm test -- proposalView.spec.tsx`
Expected: 5 passed。

- [ ] **Step 5: 全量回归**

Run: `cd web; npm test; npm run build`
Expected: 不低于 Task 9 基线 +5（121 tests）；routes.spec.tsx 下载两用例经 (h) 适配后仍绿；tsc 无错（selectedKind 删净：JSX/observer/初载均无残留引用）。

- [ ] **Step 6: 提交**

```
git add web/src/routes/ProposalView.tsx web/tests/proposalView.spec.tsx web/tests/routes.spec.tsx
```
Commit message: `feat(web): 提案阅读页全功能——版本/kind 按 id 加载与方案确认入口（审计 F13/F04）`

---

### Task 11: 主线端到端验收——stub 集成测试 + 全量回归（第二批收口）

**Files:**
- Create: `tests/test_web_mainline_e2e.py`

**Interfaces:**
- Consumes: 第二批全部用户入口——`POST /projects`、`POST /projects/{id}/messages`（回合）、`GET /projects/{id}`（快照含 `proposal_confirmed` / `proposal_confirmation_stale`）、`POST /projects/{id}/field-sites/{sid}/confirm`、`POST /projects/{id}/informants`、`PUT /projects/{id}/research-context`、`GET /projects/{id}/proposals` 与 `/projects/{id}/proposals/{doc_id}`（by-id）、`GET /projects/{id}/proposal`、`POST /projects/{id}/proposal/confirm`（Task 1-10 产出）
- Produces: 一条验收测试：空项目 → finished 全链 + 重建 repo 持久化验证 + stale 失效，覆盖 F03（对话抽取写回）/ F04（文档落盘 + 确认推进）/ F09（用户消息不重复）/ F13（by-id 正文）；F12 的 NA 豁免路径由 Task 2/6 单测覆盖，不进主线

**性质说明**：本任务为验收性质——Task 1-10 已各自 TDD，Step 1 写完即应全绿（无红灯环节）；若红，说明跨任务集成有缝，按失败点回修对应任务而非放松本测试断言。审计第二批退出条件「通过真实 FastAPI 用户入口从空项目走到 finished；刷新后状态和文档仍在；修改预算后旧确认失效」在此机械化落档。字段抽取断言用子串包含（抽取 pattern 归 Task 2 所有，e2e 不锁死具体归一化形式），但阶段推进/落盘/stale 均为硬断言。

- [ ] **Step 1: 写验收测试**

新建 `tests/test_web_mainline_e2e.py`（fixture 模式照抄 test_web_projects_api.py / test_web_state_api.py；回合等待模式照抄 test_web_turns_api.py）：

```python
# tests/test_web_mainline_e2e.py
"""主线端到端验收（审计第二批收口）：空项目 → finished 全链 + 持久化 + stale 失效。

覆盖审计第二批退出条件：通过真实 FastAPI 用户入口从空项目走到 finished；
刷新（重建 repo）后状态和文档仍在；修改预算后旧方案确认失效（stale）。
确定性 LLM stub 驱动，不联网（受控真实 API 验收另见部署手册，不入 CI）。
"""
import asyncio

import pytest
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.app import create_app
from httpx import ASGITransport, AsyncClient


class ScriptLLM:
    """回合脚本 LLM：chat_stream 逐回合弹出帧列表（test_web_agent_loop.FakeLLM
    同构）；chat() 恒返空 content——compose_proposal 内部走确定性模板降级
    （Task 4 ChatFakeLLM 模式）。sent 记录每次 chat_stream 收到的 messages，
    供 F09 断言。"""

    def __init__(self, scripts: list[list[dict]]):
        self.scripts = [list(s) for s in scripts]
        self.sent: list[list[dict]] = []

    def chat_stream(self, messages, **kwargs):
        self.sent.append(messages)
        return iter(self.scripts.pop(0))

    def chat(self, messages, **kwargs):
        return {"content": ""}


_PROPOSAL_ARGS = {
    "ctx": {
        "discipline": "社会学", "topic": "宗族变迁", "scale": "单人调研",
        "budget": "2万", "time_window": "7月",
        "departure_city": "上海", "target_location": "大理",
    },
    "sites": [{"name": "大理", "confirmed": True, "profile": {"location": "大理"}}],
    "itinerary": {"legs": []},
    "informants": [{"code": "INF-01", "type": "宗族长老", "relation": "同乡会引荐",
                    "contact": "", "notes": ""}],
}


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=None, repo=repo)
    # ASGITransport 不触发 lifespan，手动初始化依赖
    init_deps(_app, config=None, repo=repo)
    _app.state.projects_dir = tmp_path / "projects"  # ⑧ 重建验证用
    return _app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _post_message(client: AsyncClient, text: str) -> None:
    resp = await client.post("/projects/p1/messages", json={"text": text})
    assert resp.status_code == 201
    await asyncio.sleep(0.5)  # 后台回合（test_web_turns_api.py 同款等待模式）


async def test_mainline_empty_project_to_finished(app, client):
    llm = ScriptLLM([
        # 回合 1：纯文本回复（触发轮末确定性抽取，Task 2/4）
        [{"delta": "好的"}, {"content": "好的，我们开始梳理研究信息。", "usage": {}}],
        # 回合 2 第 1 帧：compose_proposal 工具调用（F04 落盘）
        [{"content": "", "tool_calls": [
            {"id": "c1", "name": "compose_proposal", "arguments": _PROPOSAL_ARGS}],
          "finish_reason": "tool_calls", "usage": {}}],
        # 回合 2 第 2 帧：收尾文本
        [{"delta": "已"}, {"content": "已生成研究方案。", "usage": {}}],
    ])

    # ① 建项：discipline/topic 直接入 ctx（A 阶段已答 2/6）
    resp = await client.post("/projects", json={"project_id": "p1",
                                                "discipline": "社会学",
                                                "topic": "宗族变迁"})
    assert resp.status_code == 201
    app.state.llm = llm

    # ② 第一回合：抽取补齐 budget/time_window/departure_city/target_location
    #（F03）→ A→B（4/6 ≥ MIN_POSITIONING_ANSWERED）
    await _post_message(client, "我从上海出发去大理调研，预算2万，2026年7月去")
    snap = (await client.get("/projects/p1")).json()
    ctx = snap["research_context"]
    assert "上海" in str(ctx.get("departure_city", ""))
    assert "大理" in str(ctx.get("target_location", ""))
    assert "2万" in str(ctx.get("budget", ""))
    assert "7月" in str(ctx.get("time_window", ""))
    assert snap["phase"] == "field_site_profiles"
    # F09：用户消息恰发送一次（历史含本条 + build 不再重复附加）
    user_texts = [str(m.get("content", "")) for m in llm.sent[0]
                  if m.get("role") == "user"]
    assert sum("上海" in t for t in user_texts) == 1

    # ③ 确认田野点：差 informant，不推进
    repo = app.state.repo
    sid = repo.save_field_site("p1", {"name": "大理", "confirmed": False,
                                      "profile": {"location": "大理"}})
    resp = await client.post(f"/projects/p1/field-sites/{sid}/confirm")
    assert resp.status_code == 200
    assert resp.json()["field_site"]["confirmed"] is True
    assert resp.json()["phase_transition"] is None

    # ④ 采纳报道人 → B→C
    resp = await client.post("/projects/p1/informants", json={
        "candidate": {"type": "宗族长老", "approach": "同乡会引荐"}})
    assert resp.status_code == 201
    assert resp.json()["informant"]["code"] == "INF-01"
    assert resp.json()["phase_transition"] == {"from": "field_site_profiles",
                                               "to": "itinerary_planning"}

    # ⑤ 行程 + travel_date/purpose → C→D（has_itinerary 走 cache_search 判定）
    repo.cache_search("p1", "itinerary::大理", "compose_itinerary",
                      {"destinations": ["大理"], "legs": []})
    resp = await client.put("/projects/p1/research-context",
                            json={"travel_date": "2026-07-10", "purpose": "课程作业"})
    assert resp.status_code == 200
    assert resp.json()["research_context"]["travel_date"] == "2026-07-10"
    assert resp.json()["phase_transition"] == {"from": "itinerary_planning",
                                               "to": "proposal_generation"}

    # ⑥ 第二回合：compose_proposal 落盘（F04）+ by-id 可读（F13）
    await _post_message(client, "生成方案")
    docs = (await client.get("/projects/p1/proposals")).json()
    assert len(docs) == 1
    assert docs[0]["kind"] == "proposal"
    latest = (await client.get("/projects/p1/proposal")).json()
    assert latest["markdown"].startswith("# ")
    by_id = (await client.get(f"/projects/p1/proposals/{docs[0]['id']}")).json()
    assert by_id["id"] == docs[0]["id"]
    assert by_id["markdown"] == latest["markdown"]
    snap = (await client.get("/projects/p1")).json()
    assert snap["phase"] == "proposal_generation"  # 草稿未确认不推进（R3 三态）

    # ⑦ 确认方案 → finished（F04）
    resp = await client.post("/projects/p1/proposal/confirm")
    assert resp.status_code == 200
    assert resp.json()["phase_transition"] == {"from": "proposal_generation",
                                               "to": "finished"}
    snap = (await client.get("/projects/p1")).json()
    assert snap["phase"] == "finished"
    assert snap["proposal_confirmed"] is True
    assert snap["proposal_confirmation_stale"] is False

    # ⑧ 持久化：重建 repo（模拟刷新/重启）后状态与文档仍在
    repo2 = JsonRepository(projects_dir=app.state.projects_dir)
    assert repo2.get_phase("p1") == "finished"
    assert len(repo2.list_proposals("p1")) == 1
    assert repo2.list_informants("p1")
    assert repo2.get_state("p1").get("proposal_confirmed") is True

    # ⑨ 修改预算 → 确认失效（stale），阶段不回退
    resp = await client.put("/projects/p1/research-context", json={"budget": "5万"})
    assert resp.status_code == 200
    snap = (await client.get("/projects/p1")).json()
    assert snap["proposal_confirmation_stale"] is True
    assert snap["phase"] == "finished"
```

- [ ] **Step 2: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_mainline_e2e.py -v`
Expected: 1 passed。若失败按失败点回修对应任务：抽取字段→Task 2/4；消息重复→Task 4；端点形状/推进→Task 6；by-id→Task 7；stale→Task 3/6。

- [ ] **Step 3: 全量回归（第二批总验收）**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 不低于 Task 10 后端基线 +1。

Run: `.venv\Scripts\python.exe -m ruff check .`
Expected: 全过。

Run: `cd web; npm test; npm run build`
Expected: 121 tests；tsc 无错。

- [ ] **Step 4: 提交**

```
git add tests/test_web_mainline_e2e.py
```
Commit message: `test(web): 主线端到端验收——空项目至 finished 全链与 stale 失效（审计第二批收口）`
