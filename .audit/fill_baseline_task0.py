# -*- coding: utf-8 -*-
"""任务0：向计划文件'基线记录'节回填基线摘要。锚点式替换，不动其他内容。"""
from pathlib import Path

p = Path("docs/plans/2026-10-18-orchestration-fixes-plan.md")
raw = p.read_bytes()
text = raw.decode("utf-8")
nl = "\r\n" if "\r\n" in text else "\n"

old_lines = [
    "## 基线记录（任务 0 执行时回填）",
    "",
    "- HEAD：",
    "- pytest：",
    "- 工作区状态：",
    "",
]
new_lines = [
    "## 基线记录（任务 0 执行时回填）",
    "",
    "- HEAD：`0f36b2b`（main）",
    "- pytest：870 passed, 3 skipped（2026-10-18，`.venv\\Scripts\\python -m pytest tests/ -q`，exit 0；3 skip 均为 Windows 符号链接权限跳过）",
    "- 工作区状态：干净（仅 untracked `.audit/`、`test-results/`）",
    "- 日期：2026-10-18",
    "",
]
old = nl.join(old_lines)
new = nl.join(new_lines)

assert text.count(old) == 1, f"anchor match={text.count(old)} (expect 1)"
text = text.replace(old, new)

# 若文件以 BOM 开头则保留原 BOM
bom = raw.startswith(b"\xef\xbb\xbf")
data = text.encode("utf-8")
if bom:
    data = b"\xef\xbb\xbf" + data
p.write_bytes(data)
print("filled ok; newline =", repr(nl), "; bom =", bom)
