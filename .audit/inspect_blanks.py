# 从生产会话 chat_history.jsonl 提取 assistant 正文，显式打印换行结构（B2 空行诊断）
import json

SRC = "/root/.fieldwork_agent/projects/field_20261018_202833/chat_history.jsonl"
OUT = "/tmp/inspect_blanks.txt"

lines_out = []
with open(SRC, encoding="utf-8") as f:
    for i, line in enumerate(f):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception as e:
            lines_out.append(f"[{i}] JSON error: {e}")
            continue
        role = rec.get("role")
        content = rec.get("content") or ""
        if role != "assistant":
            lines_out.append(f"[{i}] {role}: {len(content)} chars")
            continue
        lines_out.append(f"[{i}] assistant: {len(content)} chars")
        # repr 显示换行/空行结构（头 600 字符）
        head = content[:600]
        lines_out.append("HEAD-REPR: " + repr(head))
        # 统计连续空行出现次数
        blank_runs = 0
        prev_blank = False
        for ln in content.split("\n"):
            blank = (not ln.strip())
            if blank and not prev_blank:
                blank_runs += 1
            prev_blank = blank
        lines_out.append(f"blank-line-runs: {blank_runs}")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines_out))
print("written", OUT, len(lines_out))
