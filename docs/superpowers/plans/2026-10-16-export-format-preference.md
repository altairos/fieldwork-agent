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
- Modify: `web_host/api/exports.py`（imports + `download_proposal` 全函数，现 L1-55 文件）
- Test: `tests/test_web_exports_api.py`（追加用例，文件现为 54 行）

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

- [ ] **Step 3: 写实现**

`web_host/api/exports.py` 全量替换为：

```python
"""导出端点：提案下载（md/txt/docx，spec 2026-10-16-export-format-preference）。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response

from fieldwork_agent.web_host.api.guard import checked_project_id
from fieldwork_agent.web_host.deps import get_repo
from fieldwork_agent.web_host.export_formats import filename_for, to_docx_bytes, to_text

router = APIRouter()

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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
            content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "docx":
        docx_bytes = to_docx_bytes(content)
        if docx_bytes is not None:
            return Response(
                docx_bytes,
                media_type=DOCX_MEDIA_TYPE,
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{filename_for(filename, "docx")}"'
                    )
                },
            )
        # docx 转换失败：整体降级 txt（绝不让 500 冒泡）
    return PlainTextResponse(
        to_text(content),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename_for(filename, "txt")}"'
        },
    )
```

注意：文件顶部若有既有模块 docstring，保留原文风格；上面首行 docstring 若与现状不同，以「保留现状 docstring、只改 import 块与函数体」为准。其余路由（若文件还有别的端点）不动。

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
- Modify: `web/src/api/rest.ts`（imports 区 + L52 proposalDownloadUrl）
- Modify: `web/mock/sseServer.ts`（L13 头注释 + L242 path 解析 + download 路由 L320-331）
- Test: `web/tests/rest.spec.ts`（新建）

**Interfaces:**
- Consumes: Task 3 的 `type ExportFormat = "md" | "docx" | "txt"`
- Produces: `rest.proposalDownloadUrl(id: string, format?: ExportFormat): string`——无参或 "md" 返回 `/projects/${id}/proposal/download`（不追加 ?format=，保零破坏）；docx/txt 返回 `...?format=docx|txt`（Task 6 消费）；mock 服务器 download 路由支持三格式（Task 4 自己的 HTTP 冒烟消费）

- [ ] **Step 1: 写失败测试**

创建 `web/tests/rest.spec.ts`（vite.config.ts 已确认 vitest environment jsdom、globals true，Node 全局 fetch/Buffer 在 jsdom 环境仍可用，可做真实 HTTP 冒烟）：

```ts
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import type { Server } from "node:http";
import * as rest from "../src/api/rest";
import { startMockServer } from "../mock/sseServer";

describe("rest.proposalDownloadUrl", () => {
  test("无参：不追加 ?format=（零破坏）", () => {
    expect(rest.rest.proposalDownloadUrl("p1")).toBe("/projects/p1/proposal/download");
  });

  test("显式 md：与无参相同", () => {
    expect(rest.rest.proposalDownloadUrl("p1", "md")).toBe(
      "/projects/p1/proposal/download"
    );
  });

  test("docx：追加 ?format=docx", () => {
    expect(rest.rest.proposalDownloadUrl("p1", "docx")).toBe(
      "/projects/p1/proposal/download?format=docx"
    );
  });

  test("txt：追加 ?format=txt", () => {
    expect(rest.rest.proposalDownloadUrl("p1", "txt")).toBe(
      "/projects/p1/proposal/download?format=txt"
    );
  });
});

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

  test("默认 md：text/markdown + proposal.md + 正文含 #", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposal/download`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/markdown");
    expect(res.headers.get("content-disposition")).toContain('proposal.md');
    const text = await res.text();
    expect(text).toContain("# ");
  });

  test("txt：text/plain + proposal.txt + 正文无 #", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposal/download?format=txt`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/plain");
    expect(res.headers.get("content-disposition")).toContain('proposal.txt');
    const text = await res.text();
    expect(text).not.toContain("# ");
    expect(text).toContain("白族村落宗族组织变迁研究");
  });

  test("docx：wordprocessingml + proposal.docx + PK 魔数", async () => {
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
Expected: URL 四用例中 docx/txt 两用例 FAIL（現实现无 format 参数，TS 报错或返回无参数 URL）；mock 三用例中 txt/docx FAIL（mock 現在只有 markdown 分支，txt 拿到 markdown 响应）；md 两用例 PASS。

- [ ] **Step 3: 写实现**

`web/src/api/rest.ts`：

(a) imports 区加（放在 types import 之后）：

```ts
import type { ExportFormat } from "../lib/exportFormat";
```

(b) L52 的 `proposalDownloadUrl: (id: string) => \`/projects/${id}/proposal/download\`,` 替换为：

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

(c) download 路由（現 L320-331）替换为（保留「路由顺序红线」注释原文，新增 mock 职责边界注释）：

```ts
  // 路由顺序红线：必须在 RE_PROPOSAL（/proposal）之前匹配，否则被其截胡。
  // mock 职责边界：只复刻后端的状态码/响应头/文件名形状（docx 返回 PK 魔数、txt 轻量去标记），不引入 python-docx 逻辑。
  if (RE_DOWNLOAD.test(path)) {
    const doc = fakeProposal();
    const format = url.searchParams.get("format") ?? "md";
    if (format === "docx") {
      res.writeHead(
        200,
        {
          "Content-Type":
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "Content-Disposition": 'attachment; filename="proposal.docx"',
          ...CORS_HEADERS,
        }
      );
      res.end(Buffer.from("PK\u0003\u0004mock docx"));
      return;
    }
    if (format === "txt") {
      res.writeHead(
        200,
        {
          "Content-Type": "text/plain; charset=utf-8",
          "Content-Disposition": 'attachment; filename="proposal.txt"',
          ...CORS_HEADERS,
        }
      );
      res.end(doc.markdown.replace(/^#+\s*/gm, "").replace(/[*`]/g, ""));
      return;
    }
    res.writeHead(
      200,
      {
        "Content-Type": "text/markdown; charset=utf-8",
        "Content-Disposition": 'attachment; filename="proposal.md"',
        ...CORS_HEADERS,
      }
    );
    res.end(doc.markdown);
    return;
  }
```

注意：以現有 download 路由的实际写法为准（如 res.writeHead 参数结构不同，保持现状风格，只改出三格式分支逻辑与响应头值）；`fakeProposal()` 调用方式与現状一致。

- [ ] **Step 4: 跑测试验证通过**

Run（web/ 目录下）: `npx vitest run tests/rest.spec.ts`
Expected: 7 passed。

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
- Modify: `web/src/components/ui/icons.tsx`（文件末尾追加 IconUser）
- Create: `web/src/components/settings/MyCard.tsx`
- Modify: `web/src/components/settings/ApiKeyCard.tsx`（卡壳改造为节）
- Modify: `web/src/routes/ProjectHub.tsx`（imports L4/L10、L183 注释、L192、L329-331）
- Test: `web/tests/myCard.spec.tsx`（新建）

**Interfaces:**
- Consumes: Task 3 的 `EXPORT_FORMATS`（ExportFormatMeta[]）与 `useExportFormatStore`（format/setFormat）；既有 `ThemeSwitcher`（无 props 自订阅）与 ApiKeyCard 内部逻辑
- Produces: `export function IconUser(props: IconProps)`；`export function MyCard()`（三节：导出格式 chips + 界面配色 + API 密钥）；`export function ApiKeySection()`（原 ApiKeyCard 去卡壳；Task 6 不直接消费，但 MyCard 内部消费）

- [ ] **Step 1: 写失败测试**

创建 `web/tests/myCard.spec.tsx`（注意：「百炼 API Key（必填）」标签含嵌套徽标 span，getByText 会多匹配——改用 getByPlaceholderText("sk-…") 断言密钥输入存在）：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
      configured: true,
      models: {},
    });
    vi.mocked(rest.rest.health).mockResolvedValue({
      status: "ok",
      version: "test",
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("三节俱在：导出格式 chips + 配色入口 + 密钥节", async () => {
    render(<MyCard />);
    expect(screen.getByRole("heading", { level: 6, name: "导出格式" })).toBeDefined();
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

  test("点击 docx chip：store 与 localStorage 同步更新，选中态反转", async () => {
    const user = userEvent.setup();
    render(<MyCard />);
    const docx = screen.getByRole("button", { name: "Word 文档（.docx）" });
    await user.click(docx);
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

注意：若 `useThemeStore` 的实际导出名/路径不同（查 `web/src/stores/theme.ts`），以实际为准；settingsStatus/health 的 mock 返回形状若与 ApiKeyCard 实际消费不符（查其 useEffect），按实际字段补齐。若「导出格式」节标题未用 heading 元素，用 `screen.getByText("导出格式")` 替代 heading 查询。

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

其余内容（说明段、输入框、保存/清除按钮）不动；对应结尾的 `</div>` 层数减一（原本卡壳 div 现在直接用 `<div>` 包裹，确认闭合正确）。

(d) `web/src/routes/ProjectHub.tsx` 接线（五处）：

1. L4 `import { ApiKeyCard } from "../components/settings/ApiKeyCard";` → `import { MyCard } from "../components/settings/MyCard";`
2. L10 删除 `import { ThemeSwitcher } from "../components/ui/ThemeSwitcher";`（移入 MyCard）
3. L183 注释 `{/* 标题区（设计板 280-286）+ 配色切换（首页无 Header，入口随标题区） */}` → `{/* 标题区（设计板 280-286）——配色入口移入侧栏「我的」卡 */}`
4. L191-204 的 `<div className="flex items-center gap-2 shrink-0">` 内删掉 `<ThemeSwitcher />` 一行（保留 div 与新建项目 Button）
5. L329-331 侧栏：

```tsx
          {/* 侧栏：「我的」卡（导出格式/配色/密钥——密钥仍置顶于卡内首屏）+ 说明 */}
          <aside className="space-y-4">
            <MyCard />
```

（`<aside>` 及其后四阶段说明卡不动。）

- [ ] **Step 4: 跑测试验证通过**

Run（web/ 目录下）: `npx vitest run tests/myCard.spec.tsx`
Expected: 3 passed。

再跑全量（ProjectHub/ApiKeyCard 改动波及 workspace.spec.tsx 等既有用例）：

Run（web/ 目录下）: `npx vitest run`
Expected: 全绿。若 workspace.spec.tsx 断言了 ApiKeyCard 卡壳/标题，按新结构修断言（卡壳改为「我的」卡）。TypeScript 检查：`npx tsc --noEmit` 无报错。

- [ ] **Step 5: Commit**

```bash
git add web/src/components/ui/icons.tsx web/src/components/settings/MyCard.tsx web/src/components/settings/ApiKeyCard.tsx web/src/routes/ProjectHub.tsx web/tests/myCard.spec.tsx
git commit -m "feat(web): 「我的」卡——导出格式/配色/密钥三节首页侧栏（导出格式偏好 Task 5）" -- web/src/components/ui/icons.tsx web/src/components/settings/MyCard.tsx web/src/components/settings/ApiKeyCard.tsx web/src/routes/ProjectHub.tsx web/tests/myCard.spec.tsx
```

## Task 6: 下载入口接线（ProposalView + ProposalCard）与全量回归

**Files:**
- Modify: `web/src/routes/ProposalView.tsx`（imports + 组件体 + download 函数 + 按钮文案）
- Modify: `web/src/components/cards/ProposalCard.tsx`（红线注释 + imports + 组件体 + downloadProposal + 链接 href/文案）
- Modify: `web/tests/routes.spec.tsx`（mock 实现 + store 重置 + 新用例）
- Modify: `web/tests/toolCards.spec.tsx`（store 重置 + 新用例）

**Interfaces:**
- Consumes: Task 3 的 `useExportFormatStore`（format 只读订阅）；Task 4 的 `rest.proposalDownloadUrl(id, format?)`
- Produces: 无下游（终端任务）。验收：按钮/链接文案 `下载 .{format}`，fetch/href 按偏好追加 `?format=`；默认 md 行为与现状完全一致（含 e2e——见 Step 5 说明，e2e 无需改动）

- [ ] **Step 1: 写失败测试**

(a) `web/tests/routes.spec.tsx`：

顶部 imports 加：

```tsx
import { useExportFormatStore } from "../src/stores/exportFormat";
```

终审#1 describe 的 beforeEach 里 `localStorage.clear()` 之后加：

```tsx
    useExportFormatStore.setState({ format: "md" });
```

L78 的 proposalDownloadUrl mock 实现改为（签名扩展后原单参实现仍兼容，但显式支持 format 以覆盖新用例）：

```tsx
  vi.mocked(rest.rest.proposalDownloadUrl).mockImplementation(
    (id: string, format?: string) =>
      format && format !== "md"
        ? `/projects/${id}/proposal/download?format=${format}`
        : `/projects/${id}/proposal/download`
  );
```

在既有两用例之后追加：

```tsx
  test("偏好 docx 时：按钮文案 .docx，fetch 追加 ?format=docx", async () => {
    useExportFormatStore.setState({ format: "docx" });
    renderView();
    const btn = await screen.findByRole("button", { name: /下载 \.docx/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(calls[0][0]).toBe("/projects/p9/proposal/download?format=docx");
    expect(calls[0][1]?.headers).toMatchObject({ "X-App-Token": "tok-9" });
  });
```

（变量名 calls/createObjectURL/renderView/fireEvent/waitFor/screen 均为该文件既有绑定，直接复用；若既有用例名不同，以实际为准。）

(b) `web/tests/toolCards.spec.tsx`：

顶部 imports 加：

```tsx
import { useExportFormatStore } from "../src/stores/exportFormat";
```

既有下载用例（现 L191 附近）`localStorage.clear();` 之后加：

```tsx
    useExportFormatStore.setState({ format: "md" });
```

在既有下载用例之后追加：

```tsx
test("偏好 docx 时：链接文案与 href 追加 ?format=docx", async () => {
  localStorage.clear();
  saveKeys("", "", "tok-7");
  useExportFormatStore.setState({ format: "docx" });
  const calls: Array<[string, RequestInit | undefined]> = [];
  const createObjectURL = vi.fn(() => "blob:card-docx");
  URL.createObjectURL = createObjectURL;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(input), init]);
      return new Response(new Blob(["PK"], { type: "application/octet-stream" }));
    })
  );
  render(
    <ProposalCard
      data={cardData}
      degraded={false}
      projectId="pc1"
    />
  );
  const link = await findByRole("link", { name: /下载 \.docx/ });
  expect(link).toHaveAttribute(
    "href",
    "/projects/pc1/proposal/download?format=docx"
  );
  fireEvent.click(link);
  await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
  expect(calls[0][0]).toBe("/projects/pc1/proposal/download?format=docx");
  expect(calls[0][1]?.headers).toMatchObject({ "X-App-Token": "tok-7" });
  useExportFormatStore.setState({ format: "md" });
  vi.unstubAllGlobals();
});
```

（render/findByRole/fireEvent/waitFor 的来源与 ProposalCard 的 props 形状（cardData 构造）均照该文件既有下载用例复用；若既有用例用 getByRole 非 findBy，保持一致。）

- [ ] **Step 2: 跑测试验证失败**

Run（web/ 目录下）: `npx vitest run tests/routes.spec.tsx tests/toolCards.spec.tsx`
Expected: 两个新用例 FAIL——默认渲染文案仍是「下载 .md」，`findByRole("button", { name: /下载 \.docx/ })` 查不到元素超时；既有用例全部 PASS。

- [ ] **Step 3: 写实现**

(a) `web/src/routes/ProposalView.tsx`（五处）：

1. imports（keys 之后、types 之前）加：

```tsx
import { useExportFormatStore } from "../stores/exportFormat";
```

2. 组件体状态声明之后加：

```tsx
  // 导出格式偏好（「我的」卡设置）：只读订阅，影响下载 URL 与按钮文案
  const format = useExportFormatStore((s) => s.format);
```

3. download 函数内 `fetch(rest.proposalDownloadUrl(projectId), ...)` 改为 `fetch(rest.proposalDownloadUrl(projectId, format), ...)`
4. 下载注释（现 L121）与文件头注释中的「下载 .md」改为「下载报告（md/docx/txt，格式随「我的」偏好）」
5. 按钮（现 L198）：

```tsx
              <Button variant="primary" className="w-full" disabled={!doc} onClick={download}>
                <IconDownload className="w-4 h-4" />
                下载 .{format}
              </Button>
```

(b) `web/src/components/cards/ProposalCard.tsx`（六处）：

1. 组件头红线注释（现 L23 行后）追加例外段：

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

3. 组件体（degraded 判定前后均可，与其它局部声明并列）加：

```tsx
  // 导出格式偏好（「我的」卡设置）：只读订阅（例外说明见组件头注释）
  const format = useExportFormatStore((s) => s.format);
```

4. downloadProposal 内 `fetch(rest.proposalDownloadUrl(pid), ...)` 改为 `fetch(rest.proposalDownloadUrl(pid, format), ...)`
5. 下载链接：`href={rest.proposalDownloadUrl(pid)}` 改为 `href={rest.proposalDownloadUrl(pid, format)}`；文案 `下载 .md` 改为 `下载 .{format}`；入口注释（现 L140）改为：

```tsx
                    {/* 入口：查看（路由 /p/:id/proposal）+ 下载（格式随「我的」偏好，exports.py ?format=） */}
```

- [ ] **Step 4: 跑测试验证通过**

Run（web/ 目录下）: `npx vitest run tests/routes.spec.tsx tests/toolCards.spec.tsx`
Expected: 全部 PASS（既有 + 新增）。

- [ ] **Step 5: 全量回归 + 类型检查**

Run（web/ 目录下）: `npx vitest run`
Expected: 全绿。`npx tsc --noEmit` 无报错。

后端全量（确认 Task 2 之后无累积破坏）：仓库根 `python -m pytest tests/ -q`——全绿。

e2e 无需改动：`web/tests/e2e/fieldwork.spec.ts` 用 `/下载/i` 宽松匹配且默认偏好 md（未设置 localStorage 时 store 初始即 md），`suggestedFilename()).toMatch(/\.md$/)` 依旧成立。

- [ ] **Step 6: Commit**

```bash
git add web/src/routes/ProposalView.tsx web/src/components/cards/ProposalCard.tsx web/tests/routes.spec.tsx web/tests/toolCards.spec.tsx
git commit -m "feat(web): 下载入口按导出格式偏好请求 md/docx/txt（导出格式偏好 Task 6）" -- web/src/routes/ProposalView.tsx web/src/components/cards/ProposalCard.tsx web/tests/routes.spec.tsx web/tests/toolCards.spec.tsx
```

---

## 自审记录（writing-plans Self-Review）

1. **Spec 覆盖**：spec 各节 → 转换器（Task 1）、端点三分支/降级/422（Task 2）、偏好存储（Task 3）、URL 构造与 mock（Task 4）、「我的」卡三节/ProjectHub 接线/ApiKeyCard 改造（Task 5）、下载入口与文案（Task 6）、python-docx 只进 pyproject（GC + Task 1）、默认 md 零破坏（GC + Task 2 回归用例 + Task 4 URL 用例 + Task 6 e2e 说明）——无缺口。
2. **占位符扫描**：无 TBD/TODO/「稍后实现」；每个代码步骤含完整代码块；「以实际为准」提示均为防漂移护栏而非占位。
3. **类型一致性**：`to_text/to_docx_bytes/filename_for` Task 1 定义 = Task 2 消费；`ExportFormat` Task 3 定义 = Task 4（rest.ts import）与 Task 6（store 订阅）消费；`ApiKeySection` Task 5 内产内销；`useExportFormatStore` Task 3 定义 = Task 5/6 消费（setState 重置写法一致）。
