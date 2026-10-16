# 导出格式偏好（「我的」卡）设计

- 日期：2026-10-16
- 状态：已批准（brainstorming 设计对话定稿）
- 关联：audit-batch2 计划 Task 7（by-id 下载端点恢复执行时对接 format 参数）

## 背景与目标

提案导出目前只有 markdown 一种格式：`GET /projects/{id}/proposal/download` 返回
`text/markdown` 附件，前端三个下载入口（ProposalView 左栏按钮、ProposalCard 链接、
Header「导出」按钮）文案固定「下载 .md」。用户希望在浏览器本地设置导出格式，
可选 .md / .docx / .txt。

目标：

1. 首页侧栏新增「我的」卡，集中收纳：导出格式、界面配色（原首页标题区的
   ThemeSwitcher 移入）、API 密钥（原独立 ApiKeyCard 移入），为后续登录功能预留
   位置（本期不实现登录、不放假占位）。
2. 下载端点支持 `?format=md|docx|txt`，默认 `md`，既有调用零破坏。
3. kernel 零改动：`ProposalDoc.markdown` 是唯一真源，格式转换只发生在
   web_host 表现层。

非目标：

- 登录/账号体系（仅预留卡片结构位置）。
- PDF、LaTeX 等更多格式。
- 服务端持久化偏好：导出格式与主题、API 密钥一样，本期只存浏览器
  localStorage；日后追加登录/账号体系后，这三项本地偏好纳入服务端
  持久化偏好范畴，届时统一迁移（本期先不做）。
- 修改 kernel 的提案生成逻辑或 markdown 结构。

## 总体数据流

```
「我的」卡（chip 单选）
  → stores/exportFormat.ts（zustand）+ localStorage["fieldwork.export_format"]
  → 下载按钮文案动态化（"下载 .md" / "下载 .docx" / "下载 .txt"）
  → 下载 URL / fetch 目标追加 ?format=<fmt>（仅非 md 时追加，md 保持现状 URL）
  → FastAPI Literal["md","docx","txt"] 查询参数校验（非法值 422）
  → web_host/export_formats.py 转换器（md 原样 / txt 剥离标记 / docx 经 python-docx）
  → Content-Type 与附件扩展名随格式变化
```

偏好存储沿用 theme.ts / keys.ts 的既有模式：localStorage 持久化 + zustand store
三处同步（DOM 无需同步，仅 state + localStorage），读写均 try/catch 包裹，
隐私模式下降级为"仅本次会话生效"。

## 后端设计

### 端点签名（web_host/api/exports.py）

```python
from typing import Literal

@router.get("/projects/{project_id}/proposal/download")
async def download_proposal(
    project_id: str,
    format: Literal["md", "docx", "txt"] = "md",
    repo=Depends(get_repo),
):
```

- 默认 `md`：不传参数时响应与现状逐字节一致（text/markdown、原文件名）。
- 非法 `format` 值由 FastAPI 自动返回 422，无需手写校验。
- 参数名 `format` 遮蔽 Python 内置：FastAPI 查询参数惯例如此、与前端 URL
  参数名对齐；若与项目启用的 ruff 规则（如 flake8-builtins A002）冲突，
  加行内 noqa 注记而非改名。
- 文件名：md 沿用 `doc.get("filename", ...)` 现状；txt/docx 将扩展名替换为
  `.txt` / `.docx`（基名不变，如 `xxx_proposal.docx`）。
- Content-Type：
  - md：`text/markdown; charset=utf-8`（现状）
  - txt：`text/plain; charset=utf-8`
  - docx：`application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- 响应体：md/txt 用 `PlainTextResponse`；docx 用 `Response(content=bytes,
  media_type=...)`（二进制）。

### 转换器（新文件 web_host/export_formats.py）

独立模块，不掺入 api/ 端点文件，便于单测。三个纯函数：

```python
def to_text(markdown: str) -> str: ...          # md → txt
def to_docx_bytes(markdown: str) -> bytes: ...  # md → docx
def filename_for(base: str, fmt: str) -> str: ...  # 扩展名替换
```

**txt 转换规则**（目标是"给不装 markdown 编辑器的用户可读的纯文本"）：

- 标题行：剥离行首 `#` 序列与一个空格，保留标题文本（`## 研究设计` → `研究设计`）。
- 行内标记：`**粗体**` → `粗体`；`*斜体*` → `斜体`；`` `代码` `` → `代码`。
- 链接：`[文本](URL)` → `文本 (URL)`（保留 URL，不丢信息）。
- 列表：`- ` / `1. ` 前缀**保留**——连字符与编号本身就是通用纯文本列表写法，
  剥掉反而破坏可读性。
- 表格：剥离 `|` 与对齐行（`|---|`），单元格文本以空格分隔拼接。
- 未识别的语法原样保留。
- 空行、缩进、换行原样保留。

**docx 转换规则**（python-docx）：

- `# 标题` → Heading 1，`## 标题` → Heading 2，`### 及以下` → Heading 3/4。
- 无序列表行 → 内置 `List Bullet` 样式段落；有序列表 → `List Number`。
- 表格 → `Table Grid` 样式表格。
- 行内 `**粗体**` / `*斜体*` / `` `代码` ``（等宽字体）解析为 run 级样式。
- 字体：全部 run 通过 `w:eastAsia` 声明宋体（docx 只存字体名，服务器无需安装
  字体，Word/WPS 打开时本地解析）；西文字体用 python-docx 默认。
- 其余普通段落 → Normal。
- **降级策略**：`to_docx_bytes` 内部整体 try/except，任何异常返回 None →
  端点降级为 txt 响应（text/plain + `.txt` 文件名），不返回 500——用户至少
  拿到内容，docx 生成失败不至于让下载整个不可用。

### 依赖

- `pyproject.toml` 的 `dependencies` 增加 `python-docx>=1.1`。
- `requirements.txt` **不加**：该文件是 CLI 核心依赖子集（连 fastapi/uvicorn 都
  未列入），python-docx 与 fastapi 同属 web 宿主侧依赖。

## 前端设计

### lib/exportFormat.ts（新）

照 lib/theme.ts 的形状：

```typescript
export type ExportFormat = "md" | "docx" | "txt";
export const EXPORT_FORMAT_STORAGE_KEY = "fieldwork.export_format";
export function isExportFormat(v: unknown): v is ExportFormat;
export function readStoredFormat(): ExportFormat;  // 无值/非法回落 "md"
export const EXPORT_FORMATS: { id: ExportFormat; label: string; note: string }[];
// md: "Markdown（.md）" 默认；docx: "Word 文档（.docx）"；txt: "纯文本（.txt）"
```

### stores/exportFormat.ts（新）

照 stores/theme.ts 的形状：zustand store，`format` 初始值取
`readStoredFormat()`，`setFormat` 时同步 localStorage（try/catch）+ state。

### MyCard 组件（新 web/src/components/settings/MyCard.tsx）

卡片壳沿用 ApiKeyCard 现有手写样式（`hairline rounded-lg bg-card p-5`，
serif 标题 + 图标 + 说明段），与 settings/ 目录同放。三节自上而下：

1. **导出格式**：三个 chip 单选（md/docx/txt），点击即生效（无需保存按钮），
   附一句说明"下载最终报告时使用的文件格式，仅保存在本浏览器"。
2. **界面配色**：复用现有 ThemeSwitcher 组件（原样嵌入，不改其内部实现）。
3. **API 密钥**：原 ApiKeyCard 组件内容整体移入（ApiKeyCard 变为 MyCard 的
   一节，或直接在 MyCard 内渲染 `<ApiKeyCard />` 并去掉其外层卡片壳——
   实现时以最小 diff 为准：保留 ApiKeyCard 文件，MyCard 渲染它，ApiKeyCard
   的卡片壳改为分节小标题样式）。

### ProjectHub 接线

- 侧栏 `<aside>` 顶部（原 ApiKeyCard 位置）改为 `<MyCard />`。
- 首页标题区的 ThemeSwitcher **移除**（功能已收进 MyCard）。
- Header 里的 ThemeSwitcher **保留**（工作台/提案页的快捷入口，不动）。

### 下载入口接线

- `rest.proposalDownloadUrl(id, format?)`：加可选参数，`format !== "md"` 时
  追加 `?format=<fmt>`；md 时保持现状 URL 不变（零破坏）。
- ProposalView：`download()` 读取 store 的 format 传给
  `proposalDownloadUrl`；按钮文案 `下载 {label}`（"下载 .md" 等）随 store
  动态变化。Header「导出」按钮经 `download` 自动生效，无需改。前端 blob
  下载的 `a.download=""` 依赖响应头 Content-Disposition 提供文件名，
  docx 降级 txt 后扩展名自动跟随，无需前端感知降级。
- ProposalCard：链接 `href` 与拦截下载同样接 store；文案同步动态化。

## Mock 与测试

### mock（web/mock/sseServer.ts）

download 路由解析 `?format=`：md 原样；txt 用简化剥离（标题剥 #、行内标记
剥除即可）；docx 返回固定字节序列 + 正确 Content-Type + `.docx` 文件名。
mock 的职责是联调响应形状（状态码/头/文件名），不复刻完整转换逻辑。

### pytest

- 新 `tests/test_export_formats.py`：转换单测——txt 规则逐条断言（标题/行内/
  链接/列表保留/表格）；docx 用 `docx.Document(BytesIO(bytes))` 读回验证
  标题层级、列表样式、表格、粗体 run；`to_docx_bytes` 异常注入验证降级
  返回 None。
- `tests/test_web_exports_api.py` 扩展：`format=md` 默认响应与现状一致
  （Content-Type + 文件名断言不变）；`format=txt` / `format=docx` 的
  Content-Type、文件名、内容验证；`format=pdf` → 422。

### vitest

- 新 exportFormat store 测试：默认 md、localStorage 非法值回落 md、
  setFormat 持久化。
- MyCard 渲染测试：三节齐备、chip 点击切换 store。
- ProposalView / ProposalCard：设置 docx 后按钮文案与请求 URL 断言。
- 既有 routes.spec.tsx / theme.spec.tsx 若断言了首页标题区 ThemeSwitcher 或
  「下载 .md」文案，按新行为适配（默认仍为 md，多数断言应不变）。

## 与 audit-batch2 的时序对接

本功能先行落地。audit-batch2 恢复执行时，Task 7 规划的 by-id 下载端点
（`GET /projects/{id}/proposals/{doc_id}/download`）直接带同款 `format`
参数并复用 `web_host/export_formats.py`；届时小改 batch2 计划文档补上这一句。

## 决策记录

- python-docx 而非 pandoc/ mammoth：纯 Python 依赖、无外部二进制、写 docx
  的 API 足够覆盖标题/列表/表格/run 样式；pandoc 需要系统级安装（部署负担），
  mammoth 主打 docx→html 反向。
- 偏好只存浏览器（与服务端零交互）：与 theme/keys 模式一致，本期无账号体系，
  服务端存储无从谈起；演进路径——登录/账号体系落地后，导出格式、主题、
  API 密钥三项本地偏好纳入服务端持久化偏好范畴，统一迁移。
- 「我的」做成首页侧栏卡而非独立路由：用户明确选择（Other），同时收纳
  主题/密钥/导出格式，为登录预留。
- txt 列表前缀保留：纯文本可读性优先于"彻底剥离"的字面语义。
