# 导出格式偏好（「我的」卡）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户可在首页侧栏「我的」卡中设置最终报告（提案）导出格式（.md/.docx/.txt），下载入口按偏好请求对应格式。

**Architecture:** markdown 是唯一真源，kernel/repository 零改动；转换只发生在 web_host 表现层（新模块 `web_host/export_formats.py`）。端点 `GET /projects/{id}/proposal/download` 增加 `?format=md|docx|txt` 参数（默认 md，无参数响应与现状逐字节一致）。前端偏好存 localStorage + zustand（照 theme.ts 模式），`rest.proposalDownloadUrl` 按偏好追加查询参数。

**Tech Stack:** FastAPI（Literal 参数校验）、python-docx>=1.1（markdown→docx 转换）、pytest + httpx、React + zustand + vitest（jsdom）。

**Spec:** `docs/superpowers/specs/2026-10-16-export-format-preference-design.md`

## Global Constraints

- **markdown 唯一真源**：kernel/ 与 kernel/repository/ 零改动；转换只在 web_host 表现层。
- **默认 md 零破坏**：不带参数的下载响应与现状逐字节一致（既有 e2e `getByRole("button", { name: /下载/i })` + `suggestedFilename()).toMatch(/\.md$/)` 不需要改）；前端 md 偏好时不追加 `?format=`。
- **localStorage 键**：`fieldwork.export_format`；读取失败或值非法一律回落 `"md"`。
- **依赖**：`python-docx>=1.1` 只进 `pyproject.toml` dependencies，不进 `requirements.txt`（spec 裁决）。
- **docx 降级**：`to_docx_bytes` 任何异常整体返回 `None`，端点收到 None 降级发 txt——绝不让 500 冒泡。
- **参数名 `format`**：遮蔽内置函数，但 ruff select=["E","F","I","UP","B"] 无 A 系列规则，不触发、**不加 noqa**（spec 裁决，沿用既有参数命名风格）。
- **工作树红线**：git add 精确列出文件，禁用 `-A`/`.`；`docs/superpowers/` 在 .gitignore 内，计划/规格文档需 `git add -f`。
- **提交用 pathspec**：`git commit -m "..." -- <path>`，避免裹挟用户已暂存文件。
- **测试命令**：后端在仓库根 `python -m pytest tests/test_xxx.py -v`；前端在 `web/` 下 `npx vitest run tests/xxx.spec.ts`。
- **界面文案**：中文，禁 emoji；文案精确值以各任务代码块为准。
- **filename 时间戳**：save_proposal 生成的文件名含 `%Y%m%d_%H%M%S` 时间戳，测试断言从宽（如 `'.txt"' in cd`，不比对全名）。
- **vitest store 污染防护**：所有涉及 store 的测试文件，beforeEach 里 `localStorage.clear()` 后紧跟 `useExportFormatStore.setState({ format: "md" })`；改动态用例结束前恢复。
- **mock 职责边界**：web/mock/sseServer.ts 只复刻后端的状态码/响应头/文件名形状，docx 返回 `Buffer.from("PK\u0003\u0004mock docx")` 魔数，txt 做轻量去标记，不引入 python-docx 逻辑。

## 文件结构总览

| 文件 | 操作 | 职责 |
|---|---|---|
| `pyproject.toml` | Modify | dependencies 加 `python-docx>=1.1` |
| `web_host/export_formats.py` | Create | markdown→txt/docx 转换器 + filename_for（表现层，纯函数） |
| `tests/test_export_formats.py` | Create | 转换器单测（含 docx 读回归验、降级 None） |
| `web_host/api/exports.py` | Modify | download_proposal 加 format 参数，三分支响应 |
| `tests/test_web_exports_api.py` | Modify | 追加 5 个端点用例（txt/docx/422/降级/显式 md） |
| `web/src/lib/exportFormat.ts` | Create | ExportFormat 类型 + 存储读取（照 theme.ts） |
| `web/src/stores/exportFormat.ts` | Create | zustand 偏好 store（照 stores/theme.ts） |
| `web/tests/exportFormat.spec.ts` | Create | lib + store 单测 |
| `web/src/api/rest.ts` | Modify | proposalDownloadUrl 加可选 format 参数 |
| `web/mock/sseServer.ts` | Modify | download 路由三格式分支 |
| `web/tests/rest.spec.ts` | Create | URL 构造 + mock 服务器真实 HTTP 冒烟 |
| `web/src/components/ui/icons.tsx` | Modify | 追加 IconUser |
| `web/src/components/settings/MyCard.tsx` | Create | 「我的」卡：导出格式 chips + ThemeSwitcher + ApiKeySection 三节 |
| `web/src/components/settings/ApiKeyCard.tsx` | Modify | ApiKeyCard→ApiKeySection（卡壳移入 MyCard） |
| `web/src/routes/ProjectHub.tsx` | Modify | 侧栏接 MyCard，标题区移除 ThemeSwitcher |
| `web/tests/myCard.spec.tsx` | Create | 「我的」卡三节渲染 + chips 交互 |
| `web/src/routes/ProposalView.tsx` | Modify | 订阅偏好，下载 URL/文案随格式 |
| `web/src/components/cards/ProposalCard.tsx` | Modify | 订阅偏好（红线②例外），URL/文案随格式 |
| `web/tests/routes.spec.tsx` | Modify | ProposalView 偏好用例 |
| `web/tests/toolCards.spec.tsx` | Modify | ProposalCard 偏好用例 |

任务依赖链：Task 1→2（后端），Task 3→4→5→6（前端）。

---

## Task 1: 转换器模块 web_host/export_formats.py

**Files:**
- Modify: `pyproject.toml:12-18`（dependencies）
- Create: `web_host/export_formats.py`
- Test: `tests/test_export_formats.py`

**Interfaces:**
- Consumes: 无（纯新模块，仅标准库 + python-docx）
- Produces:
  - `to_text(markdown: str) -> str` — 标题去 `#`、行内标记还原纯文本、表格拍平为行；分隔行剔除
  - `to_docx_bytes(markdown: str) -> bytes | None` — 成功返回 docx 字节（`PK` 开头），任何异常返回 None
  - `filename_for(base: str, fmt: str) -> str` — 换扩展名（无扩展名则追加）

- [ ] **Step 1: pyproject.toml 加依赖**

`pyproject.toml` 的 dependencies（现 L12-18 附近）在 `pydantic>=2.7` 行后加一行：

```toml
    "python-docx>=1.1",
```

改完的完整块：

```toml
dependencies = [
    "dashscope>=1.20.0",
    "prompt-toolkit>=3.0.0",
    "pydantic>=2.7",
    "python-docx>=1.1",
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
]
```

安装：仓库根 `pip install -e .`（或 `.venv` 内 `pip install "python-docx>=1.1"`）。

- [ ] **Step 2: 写失败测试**

创建 `tests/test_export_formats.py`：

```python
"""web_host/export_formats.py 单测：txt 拍平、docx 结构、降级 None、filename_for。

spec：docs/superpowers/specs/2026-10-16-export-format-preference-design.md
"""
import io

from docx import Document
from docx.oxml.ns import qn

from fieldwork_agent.web_host import export_formats as ef

MD = """# 研究方案标题

## 一、背景

调研 **粗体** 与 *斜体* 与 `代码` 与 [链接文本](https://example.com/a)。

- 要点甲
- 要点乙

1. 步骤一
2. 步骤二

| 项目 | 金额 |
| --- | --- |
| 住宿 | 1800 |
| 交通 | 900 |
"""


def test_to_text_strips_heading_and_inline():
    out = ef.to_text(MD)
    assert "研究方案标题" in out
    assert "#" not in out
    assert "**" not in out
    assert "*" not in out
    assert "`" not in out


def test_to_text_keeps_list_prefixes():
    out = ef.to_text(MD)
    assert "- 要点甲" in out
    assert "1. 步骤一" in out


def test_to_text_link_keeps_url():
    out = ef.to_text(MD)
    assert "链接文本 (https://example.com/a)" in out


def test_to_text_table_flattens():
    out = ef.to_text(MD)
    assert "|" not in out
    assert "---" not in out
    assert "项目 金额" in out
    assert "住宿 1800" in out


def test_to_docx_bytes_roundtrip():
    b = ef.to_docx_bytes(MD)
    assert b is not None
    assert b[:2] == b"PK"
    doc = Document(io.BytesIO(b))
    h1 = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    h2 = [p.text for p in doc.paragraphs if p.style.name == "Heading 2"]
    assert h1 == ["研究方案标题"]
    assert h2 == ["一、背景"]
    bullets = [p.text for p in doc.paragraphs if p.style.name == "List Bullet"]
    numbers = [p.text for p in doc.paragraphs if p.style.name == "List Number"]
    assert bullets == ["要点甲", "要点乙"]
    assert numbers == ["步骤一", "步骤二"]
    assert doc.tables[0].style.name == "Table Grid"
    assert doc.tables[0].rows[0].cells[0].text == "项目"
    assert doc.tables[0].rows[2].cells[1].text == "900"
    bold_runs = [r for p in doc.paragraphs for r in p.runs if r.bold]
    assert bold_runs[0].text == "粗体"
    ea = bold_runs[0]._element.get_or_add_rPr().get_or_add_rFonts().get(qn("w:eastAsia"))
    assert ea == "宋体"


def test_to_docx_bytes_degrades_to_none(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("docx 渲染炸了")

    monkeypatch.setattr(ef, "Document", _boom)
    assert ef.to_docx_bytes(MD) is None


def test_filename_for():
    assert ef.filename_for("proposal_draft_v2.md", "md") == "proposal_draft_v2.md"
    assert ef.filename_for("proposal_draft_v2.md", "txt") == "proposal_draft_v2.txt"
    assert ef.filename_for("proposal_draft_v2.md", "docx") == "proposal_draft_v2.docx"
    assert ef.filename_for("no_ext", "txt") == "no_ext.txt"
```

- [ ] **Step 3: 跑测试验证失败**

Run: `python -m pytest tests/test_export_formats.py -v`
Expected: FAIL——`ModuleNotFoundError: No module named 'fieldwork_agent.web_host.export_formats'`（收集阶段即报）。

- [ ] **Step 4: 写最小实现**

创建 `web_host/export_formats.py`（注意：`from docx import Document` 必须在模块级——测试通过 `monkeypatch.setattr(ef, "Document", ...)` 打补丁，函数内 import 会绕开补丁）：

```python
"""markdown → 纯文本 / docx 转换器（spec：2026-10-16-export-format-preference）。

markdown 是唯一真源：本模块只做表现层转换，不触碰 kernel。
to_text：标题去 #、行内标记还原纯文本，表格拍平为行；to_docx_bytes：#/##/###
映射 Heading 1/2/3，列表/表格/行内样式落地 Word；任何异常整体降级返回 None
（端点回落 txt）；filename_for：换扩展名。docx 全 run 设 w:eastAsia=宋体
（python-docx 只设 ascii 字体时中文会回落默认字体）。
"""
import io
import re

from docx import Document
from docx.oxml.ns import qn

_INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*\n]+?\*|`[^`\n]+?`|\[[^\]\n]+\]\([^)\n]+\))")
_LINK = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ULIST = re.compile(r"^\s*[-*]\s+(.*)$")
_OLIST = re.compile(r"^\s*\d+\.\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _strip_inline(text: str) -> str:
    out: list[str] = []
    for piece in _INLINE.split(text):
        if not piece:
            continue
        m = _LINK.match(piece)
        if m:
            out.append(f"{m.group(1)} ({m.group(2)})")
        elif piece.startswith("**") and piece.endswith("**"):
            out.append(piece[2:-2])
        elif piece.startswith("*") and piece.endswith("*"):
            out.append(piece[1:-1])
        elif piece.startswith("`") and piece.endswith("`"):
            out.append(piece[1:-1])
        else:
            out.append(piece)
    return "".join(out)


def to_text(markdown: str) -> str:
    lines_out: list[str] = []
    for line in markdown.splitlines():
        if _TABLE_SEP.match(line):
            continue
        if _TABLE_ROW.match(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            lines_out.append(" ".join(c for c in cells if c))
            continue
        m = _HEADING.match(line)
        if m:
            lines_out.append(_strip_inline(m.group(2).strip()))
            continue
        lines_out.append(_strip_inline(line))
    return "\n".join(lines_out)


def _songti(run) -> None:
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")


def _styled_run(paragraph, text: str, *, bold=False, italic=False, mono=False):
    run = paragraph.add_run(text)
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    if mono:
        run.font.name = "Consolas"
    _songti(run)
    return run


def _add_inline(paragraph, text: str) -> None:
    for piece in _INLINE.split(text):
        if not piece:
            continue
        m = _LINK.match(piece)
        if m:
            _styled_run(paragraph, f"{m.group(1)} ({m.group(2)})")
        elif piece.startswith("**") and piece.endswith("**"):
            _styled_run(paragraph, piece[2:-2], bold=True)
        elif piece.startswith("*") and piece.endswith("*"):
            _styled_run(paragraph, piece[1:-1], italic=True)
        elif piece.startswith("`") and piece.endswith("`"):
            _styled_run(paragraph, piece[1:-1], mono=True)
        else:
            _styled_run(paragraph, piece)


def to_docx_bytes(markdown: str) -> bytes | None:
    try:
        doc = Document()
        table_buffer: list[list[str]] = []

        def flush_table() -> None:
            if not table_buffer:
                return
            rows = len(table_buffer)
            cols = max(len(r) for r in table_buffer)
            table = doc.add_table(rows=rows, cols=cols)
            table.style = "Table Grid"
            for i, row in enumerate(table_buffer):
                for j in range(cols):
                    table.rows[i].cells[j].text = row[j] if j < len(row) else ""
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for r in p.runs:
                            _songti(r)
            table_buffer.clear()

        for line in markdown.splitlines():
            if _TABLE_ROW.match(line):
                if not _TABLE_SEP.match(line):
                    cells = [c.strip() for c in line.strip().strip("|").split("|")]
                    table_buffer.append(cells)
                continue
            flush_table()
            if not line.strip():
                continue
            m = _HEADING.match(line)
            if m:
                p = doc.add_heading("", level=min(len(m.group(1)), 4))
                _add_inline(p, _strip_inline(m.group(2).strip()))
                continue
            m = _ULIST.match(line)
            if m:
                _add_inline(doc.add_paragraph(style="List Bullet"), m.group(1))
                continue
            m = _OLIST.match(line)
            if m:
                _add_inline(doc.add_paragraph(style="List Number"), m.group(1))
                continue
            _add_inline(doc.add_paragraph(), line)
        flush_table()
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    except Exception:
        return None


def filename_for(base: str, fmt: str) -> str:
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{stem}.{fmt}"
```

- [ ] **Step 5: 跑测试验证通过**

Run: `python -m pytest tests/test_export_formats.py -v`
Expected: 7 passed。

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml web_host/export_formats.py tests/test_export_formats.py
git commit -m "feat(web-host): markdown→txt/docx 转换器与文件名工具（导出格式偏好 Task 1）" -- pyproject.toml web_host/export_formats.py tests/test_export_formats.py
```

## Task 2: 端点 format 参数与三分支响应

**Files:**
- Modify: `web_host/api/exports.py`（imports + `download_proposal` 单函数；文件现 89 行含 5 个端点，**只动这两处，其余端点零改动**）
- Test: `tests/test_web_exports_api.py`（追加用例，文件现 86 行）

**Interfaces:**
- Consumes: Task 1 的 `to_text(markdown: str) -> str`、`to_docx_bytes(markdown: str) -> bytes | None`、`filename_for(base: str, fmt: str) -> str`（**必须用 `from fieldwork_agent.web_host.export_formats import ...` 形式导入**——降级测试用 `monkeypatch.setattr(exports_mod, "to_docx_bytes", ...)` 打模块属性补丁，`from ... import` 恰好支持此打法）
- Produces: `GET /projects/{project_id}/proposal/download?format=md|docx|txt`——md（默认）`text/markdown; charset=utf-8`；txt `text/plain; charset=utf-8`；docx `application/vnd.openxmlformats-officedocument.wordprocessingml.document`，转换失败降级 txt；非法值 422（FastAPI Literal 自动校验）

- [ ] **Step 1: 写失败测试**

`tests/test_web_exports_api.py` 在既有 `test_download_proposal` 之后追加（io/Document 在函数内 import，避免模块级依赖泄漏到纯 md 用例）：

```python
async def test_download_proposal_txt(client):
    r = await client.get("/projects/p1/proposal/download", params={"format": "txt"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert '.txt"' in cd
    assert "# 标题" not in r.text
    assert "一、背景" in r.text


async def test_download_proposal_docx(client):
    import io

    from docx import Document

    r = await client.get("/projects/p1/proposal/download", params={"format": "docx"})
    assert r.status_code == 200
    assert (
        r.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    )
    cd = r.headers["content-disposition"]
    assert ".docx" in cd
    doc = Document(io.BytesIO(r.content))
    h1 = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert h1 == ["标题"]


async def test_download_proposal_invalid_format_422(client):
    r = await client.get("/projects/p1/proposal/download", params={"format": "pdf"})
    assert r.status_code == 422


async def test_download_proposal_docx_degrades_to_txt(client, monkeypatch):
    from fieldwork_agent.web_host.api import exports as exports_mod

    monkeypatch.setattr(exports_mod, "to_docx_bytes", lambda _md: None)
    r = await client.get("/projects/p1/proposal/download", params={"format": "docx"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert '.txt"' in r.headers["content-disposition"]


async def test_download_proposal_explicit_md_matches_default(client):
    r_default = await client.get("/projects/p1/proposal/download")
    r_md = await client.get("/projects/p1/proposal/download", params={"format": "md"})
    assert r_md.status_code == r_default.status_code == 200
    assert r_md.headers["content-type"] == r_default.headers["content-type"]
    assert r_md.headers["content-disposition"] == r_default.headers["content-disposition"]
    assert r_md.content == r_default.content
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_web_exports_api.py -v`
Expected: 4 个新用例 FAIL——txt/docx 用例因无 format 参数处理而返回 markdown 响应（content-type 断言失败）；降级用例因 `exports_mod.to_docx_bytes` 属性不存在 AttributeError；显式 md 用例可能 PASS（它是回归断言，保留即可）。既有 `test_download_proposal` 必须 PASS。

- [ ] **Step 3: 写实现（增量修改，严禁全量替换——审计 F13 已给本文件加了 4 个端点）**

`web_host/api/exports.py` 只改四处，其余（含 `_proposal_payload`、`get_proposal`、`download_proposal_by_id`、`list_proposals`）零改动：

(a) 文件头部 imports：`from fastapi import ...` 之前加一行 `from typing import Literal`；`from fastapi.responses import PlainTextResponse` 改为 `from fastapi.responses import PlainTextResponse, Response`；import 块末尾加 `from fieldwork_agent.web_host.export_formats import filename_for, to_docx_bytes, to_text`

(b) `router = APIRouter(tags=["exports"])` 之后加模块级常量：

```python
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
```

(c) `download_proposal` 函数（现 L34-48）整体替换为：

```python
@router.get("/projects/{project_id}/proposal/download")
async def download_proposal(
    project_id: str,
    # 参数名沿用 spec：format（ruff 未启用 A 系列，遮蔽内置不触发，不加 noqa）
    format: Literal["md", "docx", "txt"] = "md",
    repo=Depends(get_repo),
):
    checked_project_id(project_id, repo)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    doc = repo.get_latest_proposal(project_id)
    if doc is None:
        raise HTTPException(404, "尚无提案文档")
    content = doc.get("content", "")
    filename = doc.get("filename", f"{project_id}_proposal.md")
    if format == "md":
        return PlainTextResponse(
            content, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    if format == "docx":
        docx_bytes = to_docx_bytes(content)
        if docx_bytes is not None:
            return Response(
                docx_bytes, media_type=DOCX_MEDIA_TYPE,
                headers={"Content-Disposition":
                         f'attachment; filename="{filename_for(filename, "docx")}"'})
        # docx 转换失败：整体降级 txt（绝不让 500 冒泡）
    return PlainTextResponse(
        to_text(content), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename_for(filename, "txt")}"'})
```

(d) 本文件其余四个端点与 `_proposal_payload` 零改动。

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_web_exports_api.py -v`
Expected: 全部 PASS（既有 1 个 + 新 5 个）。

再跑全量后端回归确认无破坏：

Run: `python -m pytest tests/ -q`
Expected: 全绿（本改动不触碰 kernel）。

- [ ] **Step 5: Commit**

```bash
git add web_host/api/exports.py tests/test_web_exports_api.py
git commit -m "feat(web-host): 提案下载端点支持 format=md|docx|txt（导出格式偏好 Task 2）" -- web_host/api/exports.py tests/test_web_exports_api.py
```

## Task 3: 前端偏好层 lib/exportFormat.ts + stores/exportFormat.ts

**Files:**
- Create: `web/src/lib/exportFormat.ts`
- Create: `web/src/stores/exportFormat.ts`
- Test: `web/tests/exportFormat.spec.ts`

**Interfaces:**
- Consumes: 无（纯新模块，照 `web/src/lib/theme.ts` 与 `web/src/stores/theme.ts` 同构）
- Produces:
  - `type ExportFormat = "md" | "docx" | "txt"`（Task 4/5/6 消费）
  - `EXPORT_FORMAT_STORAGE_KEY = "fieldwork.export_format"`
  - `EXPORT_FORMATS: ExportFormatMeta[]`，`ExportFormatMeta = { id: ExportFormat; label: string; note: string }`
  - `isExportFormat(v: unknown): v is ExportFormat`
  - `readStoredFormat(): ExportFormat`（非法/缺失/异常回落 "md"）
  - `useExportFormatStore`：`{ format: ExportFormat; setFormat(f: ExportFormat): void }`（Task 5/6 消费）

- [ ] **Step 1: 写失败测试**

创建 `web/tests/exportFormat.spec.ts`：

```ts
import { beforeEach, describe, expect, test } from "vitest";
import {
  EXPORT_FORMATS,
  EXPORT_FORMAT_STORAGE_KEY,
  isExportFormat,
  readStoredFormat,
} from "../src/lib/exportFormat";
import { useExportFormatStore } from "../src/stores/exportFormat";

describe("lib/exportFormat", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  test("isExportFormat：三真两假", () => {
    expect(isExportFormat("md")).toBe(true);
    expect(isExportFormat("docx")).toBe(true);
    expect(isExportFormat("txt")).toBe(true);
    expect(isExportFormat("pdf")).toBe(false);
    expect(isExportFormat(null)).toBe(false);
  });

  test("readStoredFormat：无值回落 md", () => {
    expect(readStoredFormat()).toBe("md");
  });

  test("readStoredFormat：非法值回落 md", () => {
    localStorage.setItem(EXPORT_FORMAT_STORAGE_KEY, "rtf");
    expect(readStoredFormat()).toBe("md");
  });

  test("readStoredFormat：合法值原样返回", () => {
    localStorage.setItem(EXPORT_FORMAT_STORAGE_KEY, "docx");
    expect(readStoredFormat()).toBe("docx");
  });

  test("EXPORT_FORMATS：顺序 md/docx/txt 且 label/note 完整", () => {
    expect(EXPORT_FORMATS.map((f) => f.id)).toEqual(["md", "docx", "txt"]);
    for (const f of EXPORT_FORMATS) {
      expect(f.label.length).toBeGreaterThan(0);
      expect(f.note.length).toBeGreaterThan(0);
    }
  });
});

describe("stores/exportFormat", () => {
  beforeEach(() => {
    localStorage.clear();
    useExportFormatStore.setState({ format: "md" });
  });

  test("初始态 md", () => {
    expect(useExportFormatStore.getState().format).toBe("md");
  });

  test("setFormat 同步 state 与 localStorage", () => {
    useExportFormatStore.getState().setFormat("docx");
    expect(useExportFormatStore.getState().format).toBe("docx");
    expect(localStorage.getItem(EXPORT_FORMAT_STORAGE_KEY)).toBe("docx");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（web/ 目录下）: `npx vitest run tests/exportFormat.spec.ts`
Expected: FAIL——模块不存在，resolve 报错。

- [ ] **Step 3: 写实现**

创建 `web/src/lib/exportFormat.ts`（与 theme.ts 同构：类型收窄用 ===链，读 localStorage 用 try/catch 回落；日后接入登录/账号体系后，应随其余个人偏好一并纳入服务端持久化范畴）：

```ts
/**
 * 导出格式偏好——纯函数层（与 theme.ts 同构）。
 * localStorage 键 fieldwork.export_format；读取失败或值非法一律回落 "md"。
 * 日后接入登录/账号体系后，应随其余个人偏好一并纳入服务端持久化范畴。
 */
export type ExportFormat = "md" | "docx" | "txt";

export const EXPORT_FORMAT_STORAGE_KEY = "fieldwork.export_format";

export interface ExportFormatMeta {
  id: ExportFormat;
  label: string;
  note: string;
}

export const EXPORT_FORMATS: ExportFormatMeta[] = [
  { id: "md", label: "Markdown（.md）", note: "默认 · 保留 markdown 源格式" },
  { id: "docx", label: "Word 文档（.docx）", note: "Word/WPS 直接编辑" },
  { id: "txt", label: "纯文本（.txt）", note: "无格式纯文本，任何编辑器可读" },
];

export function isExportFormat(v: unknown): v is ExportFormat {
  return v === "md" || v === "docx" || v === "txt";
}

export function readStoredFormat(): ExportFormat {
  try {
    const v = localStorage.getItem(EXPORT_FORMAT_STORAGE_KEY);
    return isExportFormat(v) ? v : "md";
  } catch {
    return "md";
  }
}
```

创建 `web/src/stores/exportFormat.ts`（照 stores/theme.ts）：

```ts
/**
 * 导出格式偏好 store（照 theme.ts：zustand create + localStorage 写入 try/catch）。
 */
import { create } from "zustand";
import {
  EXPORT_FORMAT_STORAGE_KEY,
  readStoredFormat,
  type ExportFormat,
} from "../lib/exportFormat";

interface ExportFormatState {
  format: ExportFormat;
  setFormat: (f: ExportFormat) => void;
}

export const useExportFormatStore = create<ExportFormatState>()((set) => ({
  format: readStoredFormat(),
  setFormat: (f) => {
    try {
      localStorage.setItem(EXPORT_FORMAT_STORAGE_KEY, f);
    } catch {}
    set({ format: f });
  },
}));
```

- [ ] **Step 4: 跑测试验证通过**

Run（web/ 目录下）: `npx vitest run tests/exportFormat.spec.ts`
Expected: 8 passed。

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/exportFormat.ts web/src/stores/exportFormat.ts web/tests/exportFormat.spec.ts
git commit -m "feat(web): 导出格式偏好 lib/store（导出格式偏好 Task 3）" -- web/src/lib/exportFormat.ts web/src/stores/exportFormat.ts web/tests/exportFormat.spec.ts
```

## Task 4: rest.proposalDownloadUrl 签名扩展 + mock 三分支

**Files:**
- Modify: `web/src/api/rest.ts`（imports 区 + L61 proposalDownloadUrl；f0b61ae 后文件现 82 行，紧随其后的状态修正六方法与 by-id 两方法零改动）
- Modify: `web/mock/sseServer.ts`（L13 头注释 + L242 path 解析 + download 路由 L320-331——锚点已复核未漂移）
- Test: `web/tests/rest.spec.ts`（**追加式修改**——f0b61ae 已创建该文件，现 76 行含 5 个既有用例，严禁覆盖既有用例与 stubFetch 辅助）

**Interfaces:**
- Consumes: Task 3 的 `type ExportFormat = "md" | "docx" | "txt"`
- Produces: `rest.proposalDownloadUrl(id: string, format?: ExportFormat): string`——无参或 "md" 返回 `/projects/${id}/proposal/download`（不追加 ?format=，保零破坏）；docx/txt 返回 `...?format=docx|txt`（Task 6 消费）；mock 服务器 download 路由支持三格式（Task 4 自己的 HTTP 冒烟消费）

- [ ] **Step 1: 写失败测试**

在既有 `web/tests/rest.spec.ts` 末尾追加，**既有两个 describe（5 用例）与 stubFetch/afterEach 辅助零改动**（vite.config.ts 已确认 vitest environment jsdom、globals true，Node 全局 fetch/Buffer 在 jsdom 环境仍可用，可做真实 HTTP 冒烟）。

先把文件头 imports 区（现 L2-L4 三行）合并为：

```ts
import type { Server } from "node:http";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { rest } from "../src/api/rest";
import { startMockServer } from "../mock/sseServer";
import type { ProjectSnapshot } from "../src/types/models";
```

再在文件末尾追加两个 describe（沿用现有具名导入 `rest` 与 `it` 风格，不用 `import * as` / `test`）：

```ts
describe("rest.proposalDownloadUrl", () => {
  it("无参：不追加 ?format=（零破坏）", () => {
    expect(rest.proposalDownloadUrl("p1")).toBe("/projects/p1/proposal/download");
  });

  it("显式 md：与无参相同", () => {
    expect(rest.proposalDownloadUrl("p1", "md")).toBe(
      "/projects/p1/proposal/download"
    );
  });

  it("docx：追加 ?format=docx", () => {
    expect(rest.proposalDownloadUrl("p1", "docx")).toBe(
      "/projects/p1/proposal/download?format=docx"
    );
  });

  it("txt：追加 ?format=txt", () => {
    expect(rest.proposalDownloadUrl("p1", "txt")).toBe(
      "/projects/p1/proposal/download?format=txt"
    );
  });
});
```

describe("mock 服务器 download 三格式", () => {
  let server: Server;

  beforeAll(async () => {
    server = await startMockServer(0, 5);
  });

  afterAll(() => {
    server.close();
  });

  function base(): string {
    const addr = server.address();
    if (addr === null || typeof addr === "string") {
      throw new Error("临时端口未就绪");
    }
    return `http://127.0.0.1:${addr.port}`;
  }

  it("默认 md：text/markdown + proposal.md + 正文含 #", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposal/download`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/markdown");
    expect(res.headers.get("content-disposition")).toContain('proposal.md');
    const text = await res.text();
    expect(text).toContain("# ");
  });

  it("txt：text/plain + proposal.txt + 正文无 #", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposal/download?format=txt`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/plain");
    expect(res.headers.get("content-disposition")).toContain('proposal.txt');
    const text = await res.text();
    expect(text).not.toContain("# ");
    expect(text).toContain("白族村落宗族组织变迁研究");
  });

  it("docx：wordprocessingml + proposal.docx + PK 魔数", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposal/download?format=docx`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    );
    expect(res.headers.get("content-disposition")).toContain('proposal.docx');
    const buf = Buffer.from(await res.arrayBuffer());
    expect(buf.subarray(0, 4).toString("latin1")).toBe("PK\u0003\u0004");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（web/ 目录下）: `npx vitest run tests/rest.spec.ts`
Expected: 12 用例中 4 FAIL——URL 的 docx/txt 两用例（現实现无 format 参数，JS 多余实参被忽略返回无参 URL，`?format=` 断言失败；vitest 运行时不做类型检查）与 mock 的 txt/docx 两用例（mock 現在只有 markdown 分支，txt 拿到 markdown 响应）；URL 无参/md 两用例与 mock md 用例 PASS；**既有 5 用例不受影响仍 PASS**。

- [ ] **Step 3: 写实现**

`web/src/api/rest.ts`（f0b61ae 后现 82 行）：

(a) imports 区加一行——放在 L6 `import { authHeaders } from "../lib/keys";` 之前（相对路径 import 组内字母序 exportFormat < keys）：

```ts
import type { ExportFormat } from "../lib/exportFormat";
```

(b) L61 的 `proposalDownloadUrl: (id: string) => `/projects/${id}/proposal/download`,` 替换为（其余全零改动）：

```ts
  proposalDownloadUrl: (id: string, format?: ExportFormat) =>
    format && format !== "md"
      ? `/projects/${id}/proposal/download?format=${format}`
      : `/projects/${id}/proposal/download`,
```

`web/mock/sseServer.ts`：

(a) L13 头注释行替换：

```ts
 * - GET  /projects/:id/proposal/download    → 三格式附件（?format=md|docx|txt，默认 md）
```

(b) L242 附近 `const path = new URL(req.url ?? "/", \`http://localhost:${PORT}\`).pathname;` 替换为：

```ts
  const url = new URL(req.url ?? "/", `http://localhost:${PORT}`);
  const path = url.pathname;
```

(c) download 路由（現 L320-331，已复核锚点未漂移）替换为（功能注释行更新为三格式、保留「路由顺序红线」注释原文、新增 mock 职责边界注释；保持現有 `if (method === "GET" && (m = RE_DOWNLOAD.exec(path)))` 判定与紧凑 writeHead 风格）：

```ts
  // GET /projects/:id/proposal/download → 三格式附件（?format=md|docx|txt，默认 md）。
  // 路由顺序红线：必须在 RE_PROPOSAL（/proposal）之前匹配，否则被其截胡。
  // mock 职责边界：只复刻后端的状态码/响应头/文件名形状（docx 返回 PK 魔数、txt 轻量去标记），不引入 python-docx 逻辑。
  if (method === "GET" && (m = RE_DOWNLOAD.exec(path))) {
    const doc = fakeProposal();
    const format = url.searchParams.get("format") ?? "md";
    if (format === "docx") {
      res.writeHead(200, {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": `attachment; filename="proposal.docx"`,
        ...CORS_HEADERS,
      });
      res.end(Buffer.from("PK\u0003\u0004mock docx"));
      return;
    }
    if (format === "txt") {
      res.writeHead(200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": `attachment; filename="proposal.txt"`,
        ...CORS_HEADERS,
      });
      res.end(doc.markdown.replace(/^#+\s*/gm, "").replace(/[*`]/g, ""));
      return;
    }
    res.writeHead(200, {
      "Content-Type": "text/markdown; charset=utf-8",
      "Content-Disposition": `attachment; filename="proposal.md"`,
      ...CORS_HEADERS,
    });
    res.end(doc.markdown);
    return;
  }
```

- [ ] **Step 4: 跑测试验证通过**

Run（web/ 目录下）: `npx vitest run tests/rest.spec.ts`
Expected: 12 passed（既有 5 + 新增 7）。

再跑既有回归（mock 被多个 spec 引用，确认无破坏）：

Run（web/ 目录下）: `npx vitest run`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add web/src/api/rest.ts web/mock/sseServer.ts web/tests/rest.spec.ts
git commit -m "feat(web): 下载 URL 支持格式参数 + mock 三格式分支（导出格式偏好 Task 4）" -- web/src/api/rest.ts web/mock/sseServer.ts web/tests/rest.spec.ts
```

## Task 5: 「我的」卡（MyCard）与首页接线

**Files:**
- Modify: `web/src/components/ui/icons.tsx`（文件末尾追加 IconUser；文件现 203 行，末尾区块为「移动端适配（批次 A）」IconMenu）
- Create: `web/src/components/settings/MyCard.tsx`
- Modify: `web/src/components/settings/ApiKeyCard.tsx`（卡壳改造为节；文件现 139 行，锚点 L5/L8/L28/L63-67 已复核一致）
- Modify: `web/src/routes/ProjectHub.tsx`（imports L4/L11、L185 注释、L194、L331-333——行号已按现状修正）
- Test: `web/tests/myCard.spec.tsx`（新建）

**Interfaces:**
- Consumes: Task 3 的 `EXPORT_FORMATS`（ExportFormatMeta[]）与 `useExportFormatStore`（format/setFormat）；既有 `ThemeSwitcher`（无 props 自订阅）与 ApiKeyCard 内部逻辑
- Produces: `export function IconUser(props: IconProps)`；`export function MyCard()`（三节：导出格式 chips + 界面配色 + API 密钥）；`export function ApiKeySection()`（原 ApiKeyCard 去卡壳；Task 6 不直接消费，但 MyCard 内部消费）

- [ ] **Step 1: 写失败测试**

创建 `web/tests/myCard.spec.tsx`（注意：「百炼 API Key（必填）」标签含嵌套徽标 span，getByText 会多匹配——改用 getByPlaceholderText("sk-…") 断言密钥输入存在）：

```tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import * as rest from "../src/api/rest";
import { MyCard } from "../src/components/settings/MyCard";
import { EXPORT_FORMAT_STORAGE_KEY } from "../src/lib/exportFormat";
import { useExportFormatStore } from "../src/stores/exportFormat";
import { useThemeStore } from "../src/stores/theme";

vi.mock("../src/api/rest");

describe("MyCard（「我的」卡）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useExportFormatStore.setState({ format: "md" });
    useThemeStore.setState({ theme: "cinnabar" });
    vi.mocked(rest.rest.settingsStatus).mockResolvedValue({
      dashscope_configured: true,
      flyai_configured: true,
    });
    vi.mocked(rest.rest.health).mockResolvedValue({
      status: "ok",
      auth_required: false,
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("三节俱在：导出格式 chips + 配色入口 + 密钥节", async () => {
    render(<MyCard />);
    expect(screen.getByText("导出格式")).toBeDefined();
    expect(
      screen.getByRole("button", { name: "切换界面配色" })
    ).toBeDefined();
    expect(await screen.findByPlaceholderText("sk-…")).toBeDefined();
  });

  test("导出格式三 chips：默认选中 md，其余未选中", () => {
    render(<MyCard />);
    const md = screen.getByRole("button", { name: "Markdown（.md）" });
    const docx = screen.getByRole("button", { name: "Word 文档（.docx）" });
    const txt = screen.getByRole("button", { name: "纯文本（.txt）" });
    expect(md).toHaveAttribute("aria-pressed", "true");
    expect(docx).toHaveAttribute("aria-pressed", "false");
    expect(txt).toHaveAttribute("aria-pressed", "false");
  });

  test("点击 docx chip：store 与 localStorage 同步更新，选中态反转", () => {
    render(<MyCard />);
    const docx = screen.getByRole("button", { name: "Word 文档（.docx）" });
    fireEvent.click(docx);
    expect(useExportFormatStore.getState().format).toBe("docx");
    expect(localStorage.getItem(EXPORT_FORMAT_STORAGE_KEY)).toBe("docx");
    expect(docx).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Markdown（.md）" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
  });
});
```

已核正（现状与计划的差异已并入上方代码块）：`useThemeStore` 导出名/路径无误（`web/src/stores/theme.ts`，`theme: ThemeId` 含 "cinnabar"）；settingsStatus/health mock 形状与 rest.ts 实际接口及 routes.spec.tsx 既有打法一致（`dashscope_configured`/`flyai_configured`、`status`/`auth_required`）；节标题按项目惯例用 div 非heading（MyCard/ApiKeySection 实现均为 div），测试用 `getByText`；`import * as rest` namespace 形式与 routes.spec.tsx 既有风格一致。ThemeSwitcher 触发按钮 aria-label 确为「切换界面配色」（ThemeSwitcher.tsx L45）。routes.spec.tsx 的 ProjectHub 用例只断言项目列表/空状态，不受本任务改动影响（其 settingsStatus/health mock 仅为防崩）。第三用例用 `fireEvent`——项目既有 6 个 spec 全用 fireEvent、`@testing-library/user-event` 未安装（Task 5 实现中裁决，计划同步修正）。

- [ ] **Step 2: 跑测试验证失败**

Run（web/ 目录下）: `npx vitest run tests/myCard.spec.tsx`
Expected: FAIL——模块 `../src/components/settings/MyCard` 不存在，resolve 报错。

- [ ] **Step 3: 写实现**

(a) `web/src/components/ui/icons.tsx` 文件末尾追加（照既有区块注释模式）：

```tsx
// ── 「我的」卡（导出格式偏好）追加 ──

/** 单人（「我的」卡） · 无设计板 symbol，按同一线形语言补画 */
export function IconUser(props: IconProps) {
  return (
    <Line {...props}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </Line>
  );
}
```

(b) 创建 `web/src/components/settings/MyCard.tsx`：

```tsx
/**
 * MyCard — 首页侧栏「我的」卡：导出格式 / 界面配色 / API 密钥三节。
 * 导出格式 chips 点击即生效（写 localStorage，无保存钮）；配色沿用 ThemeSwitcher
 * （卡壳从标题区移入，组件零改动）；密钥节由原 ApiKeyCard 改造（ApiKeySection）。
 * chips 样式沿用 ProjectHub 学科倾向（tokens.css .chip + 选中 cinnabar 洗色）。
 */
import { EXPORT_FORMATS } from "../../lib/exportFormat";
import { useExportFormatStore } from "../../stores/exportFormat";
import { IconUser } from "../ui/icons";
import { ThemeSwitcher } from "../ui/ThemeSwitcher";
import { ApiKeySection } from "./ApiKeyCard";

export function MyCard() {
  const format = useExportFormatStore((s) => s.format);
  const setFormat = useExportFormatStore((s) => s.setFormat);

  return (
    <div className="hairline rounded-lg bg-card p-5">
      <div className="font-serif text-[15px] font-semibold mb-1 flex items-center gap-2">
        <IconUser className="w-4 h-4 text-cinnabar" />
        我的
      </div>
      <p className="text-[11.5px] text-inksoft leading-relaxed mb-4">
        导出格式、界面配色与密钥的个人偏好，只保存在本浏览器。
      </p>

      {/* 第一节：导出格式（chips 沿用 ProjectHub 学科倾向样式） */}
      <div className="mb-4">
        <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">导出格式</div>
        <div className="flex flex-wrap gap-1.5">
          {EXPORT_FORMATS.map((f) => {
            const selected = format === f.id;
            return (
              <button
                key={f.id}
                type="button"
                aria-pressed={selected}
                title={f.note}
                onClick={() => setFormat(f.id)}
                className={`chip transition ${
                  selected
                    ? "bg-cinnabarwash text-cinnabar border-cinnabar/25"
                    : "bg-card hairline text-inksoft hover:bg-cardsunk"
                }`}
              >
                {f.label}
              </button>
            );
          })}
        </div>
        <div className="text-[10.5px] text-inkfaint mt-2">
          下载最终报告时使用的文件格式，仅保存在本浏览器。
        </div>
      </div>

      {/* 第二节：界面配色（ThemeSwitcher 原样嵌入，零改动） */}
      <div className="mb-4">
        <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">界面配色</div>
        <ThemeSwitcher />
      </div>

      {/* 第三节：API 密钥（原 ApiKeyCard 改造，卡壳已上移至本卡） */}
      <ApiKeySection />
    </div>
  );
}
```

(c) `web/src/components/settings/ApiKeyCard.tsx` 改造（四处）：

1. L5 `import { IconCheck, IconLock } from "../ui/icons";` → `import { IconCheck } from "../ui/icons";`（IconLock 随卡壳标题移除而不再使用）
2. L8 头注释首行 `* ApiKeyCard — 首页侧栏「API 密钥」卡。` → `* ApiKeySection —「我的」卡（MyCard）的 API 密钥节（原独立卡改造，卡壳移入 MyCard）。`
3. L28 `export function ApiKeyCard() {` → `export function ApiKeySection() {`
4. L63-67 卡壳 + 标题（`<div className="hairline rounded-lg bg-card p-5">` + font-serif 标题行含 IconLock「API 密钥」）替换为节标题：

```tsx
    <div>
      <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">API 密钥</div>
```

其余内容（说明段、输入框、保存/清除按钮）不动；结尾 L136 `</div>` 闭合不变——外层 div 仅由卡壳样式类换成裸 `<div>`，总层数不变。

(d) `web/src/routes/ProjectHub.tsx` 接线（五处）：

1. L4 `import { ApiKeyCard } from "../components/settings/ApiKeyCard";` → `import { MyCard } from "../components/settings/MyCard";`
2. L11 删除 `import { ThemeSwitcher } from "../components/ui/ThemeSwitcher";`（移入 MyCard）
3. L185 注释 `{/* 标题区（设计板 280-286）+ 配色切换（首页无 Header，入口随标题区） */}` → `{/* 标题区（设计板 280-286）——配色入口移入侧栏「我的」卡 */}`
4. L193-206 的 `<div className="flex items-center gap-2 shrink-0">` 内删掉 L194 `<ThemeSwitcher />` 一行（保留 div 与新建项目 Button）
5. L331-333 侧栏（替换其上的旧注释 + `<ApiKeyCard />` 一行）：

```tsx
          {/* 侧栏：「我的」卡（导出格式/配色/密钥——密钥仍置顶于卡内首屏）+ 说明 */}
          <aside className="space-y-4">
            <MyCard />
```

（`<aside>` 及其后四阶段说明卡不动。）

- [ ] **Step 4: 跑测试验证通过**

Run（web/ 目录下）: `npx vitest run tests/myCard.spec.tsx`
Expected: 3 passed。

再跑全量（ProjectHub/ApiKeyCard 改动波及既有用例）：

Run（web/ 目录下）: `npx vitest run`
Expected: 全绿。已预核：routes.spec.tsx 的 ProjectHub 用例只断言项目列表/空状态（不断言密钥卡壳/配色入口），workspace.spec.tsx 无 ApiKeyCard 卡壳断言——均无需改断言；若仍有用例因「我的」卡新结构失败，按新结构修该用例断言。TypeScript 检查：`npx tsc --noEmit` 无报错。

- [ ] **Step 5: Commit**

```bash
git add web/src/components/ui/icons.tsx web/src/components/settings/MyCard.tsx web/src/components/settings/ApiKeyCard.tsx web/src/routes/ProjectHub.tsx web/tests/myCard.spec.tsx
git commit -m "feat(web): 「我的」卡——导出格式/配色/密钥三节首页侧栏（导出格式偏好 Task 5）" -- web/src/components/ui/icons.tsx web/src/components/settings/MyCard.tsx web/src/components/settings/ApiKeyCard.tsx web/src/routes/ProjectHub.tsx web/tests/myCard.spec.tsx
```

## Task 6: 下载入口接线（by-id 链路扩围 + ProposalView + ProposalCard）与全量回归

> **漂移修正（2026-10-16）**：初稿后主线 6dd56e8（web 提案阅读页）落地——ProposalView 主下载入口已改走 by-id 端点 `GET /projects/{id}/proposals/{docId}/download`（F13 版本审计修复），该端点当前 md-only 无 format 参数；Header 新增 onExport 入口但复用组件 download 函数（按钮文案「导出」无格式后缀，**Header 零改动**；其头注释「下载 .md」系设计板 line 243 溯源引用，非行为描述，不动）。偏好若不覆盖 by-id 链路，阅读页（主入口）偏好失效——本任务据此扩围至后端 by-id 端点、rest.ts、mock 与前端双入口。ProposalCard 仍走 latest 端点（Task 4 双参直接适用）。

**Files:**
- Modify: `web_host/api/exports.py`（提取三分支共用 helper；by-id 端点加 format 参数）
- Modify: `tests/test_web_exports_api.py`（by-id txt/docx 两用例）
- Modify: `web/src/api/rest.ts`（proposalByIdDownloadUrl 加第三可选参 format）
- Modify: `web/tests/rest.spec.ts`（by-id URL 构造断言扩为独立 describe + mock by-id download 两用例）
- Modify: `web/mock/sseServer.ts`（by-id download mock 分支）
- Modify: `web/src/routes/ProposalView.tsx`（imports + store 订阅 + by-id 三参 + 注释 + 按钮文案）
- Modify: `web/src/components/cards/ProposalCard.tsx`（红线例外段 + imports + 订阅 + downloadProposal/href 双参 + 文案与入口注释）
- Modify: `web/tests/routes.spec.tsx`（store 重置 + by-id mock 三参 + 新用例）
- Modify: `web/tests/toolCards.spec.tsx`（store 重置 + 新用例）

**Interfaces:**
- Consumes: Task 3 的 `useExportFormatStore`（format 只读订阅）；Task 4 的 `rest.proposalDownloadUrl(id, format?)`；Task 1 的 `web_host/export_formats`（to_docx_bytes/to_text/filename_for——by-id 端点经共用 helper 复用）
- Produces: 无下游（终端任务）。验收：ProposalView 按钮 / ProposalCard 链接文案 `下载 .{format}`，fetch / href 按偏好追加 `?format=`（by-id 与 latest 两链路）；后端两下载端点均支持 `?format=md|docx|txt`（docx 失败降级 txt）；默认 md 行为与现状完全一致（含 e2e——见 Step 5 说明，e2e 无需改动）

- [ ] **Step 1: 写失败测试**

(a) `tests/test_web_exports_api.py`——在既有 `test_download_by_id`（现 L124-130）之后、`test_list_created_at_uses_saved_at` 之前追加（fixture 内容 `# 标题\n\n## 一、背景\n\n内容`，与 latest 端点既有用例同源）：

```python
async def test_download_by_id_txt(client, app):
    repo = app.state.repo
    docs = repo.list_proposals("p1")
    r = await client.get(f"/projects/p1/proposals/{docs[0]['id']}/download",
                         params={"format": "txt"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert '.txt"' in cd
    assert "# 标题" not in r.text
    assert "一、背景" in r.text


async def test_download_by_id_docx(client, app):
    import io

    from docx import Document

    repo = app.state.repo
    docs = repo.list_proposals("p1")
    r = await client.get(f"/projects/p1/proposals/{docs[0]['id']}/download",
                         params={"format": "docx"})
    assert r.status_code == 200
    assert (
        r.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    )
    cd = r.headers["content-disposition"]
    assert ".docx" in cd
    doc = Document(io.BytesIO(r.content))
    h1 = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert h1 == ["标题"]
```

(b) `web/tests/rest.spec.ts`：

既有用例「getProposalById 与下载 URL 指向 by-id 端点」（现 L53-60）内的双参断言保持不变；在其 describe 收尾 `});` 之后、`rest.proposalDownloadUrl` describe 之前追加：

```ts
describe("rest.proposalByIdDownloadUrl", () => {
  it("双参：不追加 ?format=（零破坏）", () => {
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1")).toBe(
      "/projects/p1/proposals/prop_1/download"
    );
  });

  it("显式 md：与双参相同", () => {
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1", "md")).toBe(
      "/projects/p1/proposals/prop_1/download"
    );
  });

  it("docx：追加 ?format=docx", () => {
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1", "docx")).toBe(
      "/projects/p1/proposals/prop_1/download?format=docx"
    );
  });

  it("txt：追加 ?format=txt", () => {
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1", "txt")).toBe(
      "/projects/p1/proposals/prop_1/download?format=txt"
    );
  });
});
```

「mock 服务器 download 三格式」describe 内既有三用例（现 L122-150）之后、describe 收尾前追加：

```ts
  it("by-id 默认 md：text/markdown + attachment", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposals/doc_1/download`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/markdown");
    expect(res.headers.get("content-disposition")).toContain("attachment");
    const text = await res.text();
    expect(text).toContain("# ");
  });

  it("by-id docx：wordprocessingml + PK 魔数", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposals/doc_1/download?format=docx`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    );
    expect(res.headers.get("content-disposition")).toContain(".docx");
    const buf = Buffer.from(await res.arrayBuffer());
    expect(buf.subarray(0, 4).toString("latin1")).toBe("PK\u0003\u0004");
  });
```

(c) `web/tests/routes.spec.tsx`（终审#1 describe）：

顶部 imports 加：

```tsx
import { useExportFormatStore } from "../src/stores/exportFormat";
```

终审#1 describe 的 beforeEach 里 `localStorage.clear()`（现 L65）之后加：

```tsx
    useExportFormatStore.setState({ format: "md" });
```

L78 的 proposalDownloadUrl mock 为历史遗留（组件已改走 by-id），**保留不动**；L79-81 的 by-id mock 实现扩为三参（与 rest.ts 签名同构）：

```tsx
    vi.mocked(rest.rest.proposalByIdDownloadUrl).mockImplementation(
      (id: string, docId: string, format?: string) =>
        format && format !== "md"
          ? `/projects/${id}/proposals/${docId}/download?format=${format}`
          : `/projects/${id}/proposals/${docId}/download`,
    );
```

既有两用例断言保持不变（默认 md → `/projects/p9/proposals/doc1/download`）。在既有两用例之后追加：

```tsx
  test("偏好 docx 时：按钮文案 .docx，by-id fetch 追加 ?format=docx", async () => {
    useExportFormatStore.setState({ format: "docx" });
    renderView();
    const btn = await screen.findByRole("button", { name: /下载 \.docx/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(calls[0][0]).toBe("/projects/p9/proposals/doc1/download?format=docx");
    expect(calls[0][1]?.headers).toMatchObject({ "X-App-Token": "tok-9" });
    useExportFormatStore.setState({ format: "md" });
  });
```

（变量名 calls/createObjectURL/renderView/fireEvent/waitFor/screen 均为该文件既有绑定，直接复用；末行恢复默认偏好，避免污染同文件后续用例。）

(d) `web/tests/toolCards.spec.tsx`（终审#1 describe）：

顶部 imports 加：

```tsx
import { useExportFormatStore } from "../src/stores/exportFormat";
```

既有下载用例（现 L191 附近）`localStorage.clear();` 之后加：

```tsx
    useExportFormatStore.setState({ format: "md" });
```

在该用例之后（describe 收尾前）追加（复刻既有用例的替身注入与内联 data 风格，fireEvent 非 userEvent）：

```tsx
  test("偏好 docx 时：链接文案与 href 追加 ?format=docx", async () => {
    localStorage.clear();
    saveKeys("", "", "tok-7");
    useExportFormatStore.setState({ format: "docx" });
    const calls: Array<[string, RequestInit | undefined]> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return { ok: true, blob: async () => new Blob(["# 研究方案"]) } as unknown as Response;
    }));
    // jsdom 未实现 createObjectURL/revokeObjectURL，注入可断言替身
    const createObjectURL = vi.fn(() => "blob:card-docx");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true, writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true, writable: true });

    render(<ProposalCard data={{ markdown: "# 研究方案\n", kind: "proposal", version: "draft", sections: ["研究定位"] }} projectId="pc1" />);
    const link = screen.getByRole("link", { name: /下载 \.docx/ });
    expect(link).toHaveAttribute("href", "/projects/pc1/proposal/download?format=docx");
    fireEvent.click(link);

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(calls[0][0]).toBe("/projects/pc1/proposal/download?format=docx");
    expect(calls[0][1]?.headers).toMatchObject({ "X-App-Token": "tok-7" });
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:card-docx");
    useExportFormatStore.setState({ format: "md" });
    vi.unstubAllGlobals();
  });
```

- [ ] **Step 2: 跑测试验证失败**

Run（仓库根）: `python -m pytest tests/test_web_exports_api.py -q`
Expected: 新增两用例 FAIL（by-id 端点现返回 text/markdown，txt 用例的 content-type/正文断言、docx 用例的 content-type 断言不成立）；既有用例全部 PASS。

Run（web/ 目录下）: `npx vitest run tests/rest.spec.ts tests/routes.spec.tsx tests/toolCards.spec.tsx`
Expected: 新用例 FAIL——`proposalByIdDownloadUrl("p1","prop_1","docx")` 仍返回无参 URL；routes/toolCards 的 docx 用例因文案仍是「下载 .md」查不到元素超时（或 href 断言不成立）；既有用例全部 PASS。

- [ ] **Step 3: 写实现**

(a) `web_host/api/exports.py`：

将 download_proposal 尾部三分支（现 L56-70 的 md/docx/txt 响应构造）提取为模块级 helper（放在 download_proposal 装饰器之前）：

```python
def _format_proposal_response(content: str, format: str, filename: str) -> Response:
    """md/docx/txt 三分支持（spec §3.2；docx 失败整体降级 txt，绝不让 500 冒泡）。"""
    if format == "md":
        return PlainTextResponse(
            content, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    if format == "docx":
        docx_bytes = to_docx_bytes(content)
        if docx_bytes is not None:
            return Response(
                docx_bytes, media_type=DOCX_MEDIA_TYPE,
                headers={"Content-Disposition":
                         f'attachment; filename="{filename_for(filename, "docx")}"'})
        # docx 转换失败：整体降级 txt（绝不让 500 冒泡）
    return PlainTextResponse(
        to_text(content), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename_for(filename, "txt")}"'})
```

download_proposal 尾部（原 L55-70）改为：

```python
    filename = doc.get("filename", f"{project_id}_proposal.md")
    return _format_proposal_response(content, format, filename)
```

by-id 端点（现 L86-99）签名加 format 参数、尾部改调 helper：

```python
@router.get("/projects/{project_id}/proposals/{doc_id}/download")
async def download_proposal_by_id(
    project_id: str,
    doc_id: str,
    # 参数名沿用 spec：format（ruff 未启用 A 系列，遮蔽内置不触发，不加 noqa）
    format: Literal["md", "docx", "txt"] = "md",
    repo=Depends(get_repo),
):
    checked_project_id(project_id, repo)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    doc = repo.get_proposal_by_id(project_id, doc_id)
    if doc is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    filename = doc.get("filename", f"{project_id}_{doc_id}.md")
    return _format_proposal_response(doc.get("content", ""), format, filename)
```

（注意：degrades 用例 monkeypatch 的是 `exports_mod.to_docx_bytes`——提取 helper 后该调用仍在模块顶层命名空间内（helper 内直接引用），monkeypatch 依旧生效，无需改既有用例。）

(b) `web/src/api/rest.ts`：`proposalByIdDownloadUrl`（现 L83-84）扩为三参（与上方 proposalDownloadUrl 同构）：

```ts
  proposalByIdDownloadUrl: (id: string, docId: string, format?: ExportFormat) =>
    format && format !== "md"
      ? `/projects/${id}/proposals/${docId}/download?format=${format}`
      : `/projects/${id}/proposals/${docId}/download`,
```

(c) `web/mock/sseServer.ts`：

路由正则区 RE_DOWNLOAD 行（现 L236）后加：

```ts
const RE_BY_ID_DOWNLOAD = /^\/projects\/([^/]+)\/proposals\/([^/]+)\/download$/;
```

（路由顺序：`/proposals/:docId/download` 与 RE_PROPOSALS（`/proposals$`）、RE_PROPOSAL（`/proposal$`）、RE_PROJECT（`/projects/:id$`）均不重叠，无截胡风险。）

处理段在 RE_DOWNLOAD 分支（现 L324-353）之后、RE_PROPOSAL 分支之前插入（复刻三格式，职责边界同前：只复刻状态码/响应头/文件名形状；文件名用 proposal.* 即可，不逐字复刻后端 `${project_id}_${doc_id}` 默认名）：

```ts
  // GET /projects/:id/proposals/:docId/download → 三格式附件（by-id 端点，ProposalView 主入口）。
  if (method === "GET" && (m = RE_BY_ID_DOWNLOAD.exec(path))) {
    const doc = fakeProposal();
    const format = url.searchParams.get("format") ?? "md";
    if (format === "docx") {
      res.writeHead(200, {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": `attachment; filename="proposal.docx"`,
        ...CORS_HEADERS,
      });
      res.end(Buffer.from("PK\u0003\u0004mock docx"));
      return;
    }
    if (format === "txt") {
      res.writeHead(200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": `attachment; filename="proposal.txt"`,
        ...CORS_HEADERS,
      });
      res.end(doc.markdown.replace(/^#+\s*/gm, "").replace(/[*`]/g, ""));
      return;
    }
    res.writeHead(200, {
      "Content-Type": "text/markdown; charset=utf-8",
      "Content-Disposition": `attachment; filename="proposal.md"`,
      ...CORS_HEADERS,
    });
    res.end(doc.markdown);
    return;
  }
```

(d) `web/src/routes/ProposalView.tsx`（五处）：

1. imports（keys 之后、types 之前）加：

```tsx
import { useExportFormatStore } from "../stores/exportFormat";
```

2. 组件体状态声明（现 L44-56 十二个 useState/useEffect 声明）之后加：

```tsx
  // 导出格式偏好（「我的」卡设置）：只读订阅，影响下载 URL 与按钮文案
  const format = useExportFormatStore((s) => s.format);
```

3. download 函数内（现 L138）`fetch(rest.proposalByIdDownloadUrl(projectId, doc.id), ...)` 改为 `fetch(rest.proposalByIdDownloadUrl(projectId, doc.id, format), ...)`
4. 下载注释（现 L131-133）与文件头注释（现 L14）中的「下载 .md」改为「下载报告（md/docx/txt，格式随「我的」偏好）」
5. 按钮（现 L252-255）文案改 `下载 .{format}`：

```tsx
              <Button variant="primary" className="w-full" disabled={!doc} onClick={download}>
                <IconDownload className="w-4 h-4" />
                下载 .{format}
              </Button>
```

（Header 的 onExport 复用 download 函数，零改动。）

(e) `web/src/components/cards/ProposalCard.tsx`（六处；注意：工作树含用户未提交的 MODE_LABEL 体例标签改动（现 L35-41/L122-128 区域），与本任务锚点零重叠，将随本任务一并裹挟入库，同 Task 5 ProjectHub 先例）：

1. 组件头 JSDoc（现 L10-24）红线②行（现 L21「红线②：卡片不订阅 store、不在挂载期发请求——」）之后、JSDoc 收尾 `*/`（现 L24）之前插入例外段：

```tsx
 * 例外（2026-10-16 导出格式偏好）：本卡订阅 useExportFormatStore 读纯本地
 * 偏好（无请求、无异步），仅影响下载链接的 URL 与文案。红线动机是禁挂载期
 * 请求与禁臆造数据，不禁同步读取本地偏好（ThemeSwitcher 直接订阅是先例；
 * 经 props 注入会污染 Timeline 十四卡泛型分发层，故取订阅而非注入）。
```

2. imports（rest import 之后）加：

```tsx
import { useExportFormatStore } from "../../stores/exportFormat";
```

3. 组件体（pid 声明（现 L74）附近，与其它局部声明并列）加：

```tsx
  // 导出格式偏好（「我的」卡设置）：只读订阅（例外说明见组件头注释）
  const format = useExportFormatStore((s) => s.format);
```

4. downloadProposal 内（现 L93）`fetch(rest.proposalDownloadUrl(pid), ...)` 改为 `fetch(rest.proposalDownloadUrl(pid, format), ...)`
5. 下载链接：`href={rest.proposalDownloadUrl(pid)}`（现 L157）改为 `href={rest.proposalDownloadUrl(pid, format)}`；文案 `下载 .md`（现 L162）改为 `下载 .{format}`；入口注释（现 L147）改为：

```tsx
                    {/* 入口：查看（路由 /p/:id/proposal）+ 下载（格式随「我的」偏好，exports.py ?format=） */}
```

- [ ] **Step 4: 跑测试验证通过**

Run（仓库根）: `python -m pytest tests/test_web_exports_api.py -q`
Run（web/ 目录下）: `npx vitest run tests/rest.spec.ts tests/routes.spec.tsx tests/toolCards.spec.tsx`
Expected: 全部 PASS（既有 + 新增）。

- [ ] **Step 5: 全量回归 + 类型检查**

Run（web/ 目录下）: `npx vitest run`
Expected: 全绿。`npx tsc --noEmit` 无报错。

后端全量（确认 Task 2 之后无累积破坏）：仓库根 `python -m pytest tests/ -q`——全绿。

e2e 无需改动：`web/tests/e2e/fieldwork.spec.ts` 用 `/下载/i` 宽松匹配且默认偏好 md（未设置 localStorage 时 store 初始即 md），`suggestedFilename()).toMatch(/\.md$/)` 依旧成立；by-id 链路默认 md 同理。

- [ ] **Step 6: Commit**

```bash
git add web_host/api/exports.py tests/test_web_exports_api.py web/src/api/rest.ts web/tests/rest.spec.ts web/mock/sseServer.ts web/src/routes/ProposalView.tsx web/src/components/cards/ProposalCard.tsx web/tests/routes.spec.tsx web/tests/toolCards.spec.tsx
git commit -m "feat: 下载入口按导出格式偏好请求 md/docx/txt——by-id 端点扩 format（导出格式偏好 Task 6）" -- web_host/api/exports.py tests/test_web_exports_api.py web/src/api/rest.ts web/tests/rest.spec.ts web/mock/sseServer.ts web/src/routes/ProposalView.tsx web/src/components/cards/ProposalCard.tsx web/tests/routes.spec.tsx web/tests/toolCards.spec.tsx
```

（ProposalCard.tsx 入库时含用户工作树未提交的 MODE_LABEL 体例标签改动——与本任务锚点零重叠，属预期裹挟，同 Task 5 ProjectHub 先例；若届时工作树另有新增改动，implementer 须停下报告而非自行裁决。）

---

## 自审记录（writing-plans Self-Review）

1. **Spec 覆盖**：spec 各节 → 转换器（Task 1）、端点三分支/降级/422（Task 2）、偏好存储（Task 3）、URL 构造与 mock（Task 4）、「我的」卡三节/ProjectHub 接线/ApiKeyCard 改造（Task 5）、下载入口与文案（Task 6）、python-docx 只进 pyproject（GC + Task 1）、默认 md 零破坏（GC + Task 2 回归用例 + Task 4 URL 用例 + Task 6 e2e 说明）——无缺口。
2. **占位符扫描**：无 TBD/TODO/「稍后实现」；每个代码步骤含完整代码块；「以实际为准」提示均为防漂移护栏而非占位。
3. **类型一致性**：`to_text/to_docx_bytes/filename_for` Task 1 定义 = Task 2 消费；`ExportFormat` Task 3 定义 = Task 4（rest.ts import）与 Task 6（store 订阅）消费；`ApiKeySection` Task 5 内产内销；`useExportFormatStore` Task 3 定义 = Task 5/6 消费（setState 重置写法一致）。
