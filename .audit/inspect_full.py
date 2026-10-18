# 输出生产会话 assistant 消息的全文行结构（B2 空行诊断第 2 步）
import json

SRC = "/root/.fieldwork_agent/projects/field_20261018_202833/chat_history.jsonl"
OUT = "/tmp/inspect_full.txt"

lines_out = []
with open(SRC, encoding="utf-8") as f:
    for i, line in enumerate(f):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        content = rec.get("content") or ""
        if rec.get("role") != "assistant":
            continue
        if i not in (3, 11, 17):  # 深看三条高 blank-runs 的消息
            continue
        lines_out.append(f"===== [{i}] assistant =====")
        for j, ln in enumerate(content.split("\n")):
            lines_out.append(f"{j:3d}| {ln!r}")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines_out))
print("written", OUT, len(lines_out))
