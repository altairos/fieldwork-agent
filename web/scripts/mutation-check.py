"""变异自证：逐条还原修复，确认对应护栏变红，再还原回绿。

    python scripts/mutation-check.py        # 需要 NODE_BIN 或 PATH 里有 node

为什么需要它（不是形式主义）：本次写护栏时踩到两次「测试看着绿、其实没咬人」——
1. 给流式正文加节流后测试仍绿，因为 MarkdownBody 没 memo，每次都重解析（节流白做）；
2. R1 的变异最初打在 read 之后，而 bug 位置在 fetch 之后 read 之前，变异不可观测、
   测试照样全绿，差点把一个假护栏当成真护栏提交。

每条变异只跑它该打红的那个测试文件；跑完无论成败都会还原，并断言文件已逐字节还原。
"""
import os
import re
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # web/
# Windows 下 npx 是 .cmd，python 直接调不到；直接用 node 跑 vitest 的入口脚本。
NODE = os.environ.get("NODE_BIN") or shutil.which("node") or "node"

MUTATIONS = [
    ("P1-2 活动条回到硬编码名单", "src/components/chat/ToolActivityStrip.tsx",
     '.filter((c) => !isHiddenTool(c.tool) && !(c.status === "done" && !c.degraded));',
     '.filter((c) => c.tool !== "update_research_context" && !(c.status === "done" && !c.degraded));',
     "tests/activityStrip.spec.tsx"),
    ("P1-1 证据行一律标「本轮」", "src/lib/evidenceFilter.ts",
     'return row.turnId && currentTurnId && row.turnId === currentTurnId ? "本轮" : "本次会话";',
     'return "本轮";',
     "tests/evidencePanel.spec.tsx"),
    ("R1 退避在 fetch 成功时即重置", "src/api/sse.ts",
     'const reader = resp.body.getReader();',
     'reconnects = 0;   // 原 bug：连上即重置\n      const reader = resp.body.getReader();',
     "tests/sse.spec.ts"),
    ("R2 去掉空闲看门狗", "src/api/sse.ts",
     "return await Promise.race([reader.read(), timeout]);",
     "return await reader.read();",
     "tests/sse.spec.ts"),
    ("R3 帧切分不兼容 CRLF", "src/api/sse.ts",
     'buffer.replace(/\\r\\n?/g, "\\n").split("\\n\\n")',
     'buffer.split("\\n\\n")',
     "tests/sse.spec.ts"),
    ("R4 在飞守卫退回 busy（异步态）", "src/hooks/useTurn.ts",
     "if (!text.trim() || inFlightRef.current) return;",
     "if (!text.trim() || busy) return;",
     "tests/useTurn.spec.tsx"),
    ("R6 Tabs 去掉方向键处理", "src/components/ui/Tabs.tsx",
     "      onKeyDown={onKeyDown}\n",
     "",
     "tests/tabsA11y.spec.tsx"),
    ("R7 groups 变化不清空选中", "src/components/chat/QuickReplies.tsx",
     "  }, [signature]);",
     "  }, []);",
     "tests/quickReplies.spec.tsx"),
    ("P1-3 stores 重新依赖组件层", "src/stores/ui.ts",
     'import { phaseLabel } from "../lib/phases";',
     'import { phaseLabel } from "../lib/phases";\nimport { getToolCard } from "../components/cards/registry";',
     "tests/toolMeta.spec.ts"),
]


def run(test_file):
    p = subprocess.run(
        [NODE, os.path.join(ROOT, "node_modules", "vitest", "vitest.mjs"),
         "run", test_file],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = p.stdout + p.stderr
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)
    failed = re.search(r"Tests\s+\d+ failed", out)
    names = re.findall(r"FAIL\s+(\S+)\s*>\s*(.+)$", out, re.M)
    reason = re.search(r"(AssertionError:.+|Error: .+)", out)
    summary = f"{failed.group(0) if failed else 'no-summary'}; 用例：{(names[0][1][:40] if names else '?')}; 原因：{(reason.group(1)[:60] if reason else '?')}"
    return p.returncode, summary, out


for name, rel, old, new, test_file in MUTATIONS:
    path = os.path.join(ROOT, rel)
    original = open(path, encoding="utf-8").read()
    if old not in original:
        print(f"[SKIP] {name}: 未找到锚点 {old[:50]!r}")
        continue
    shutil.copyfile(path, path + ".bak")
    try:
        open(path, "w", encoding="utf-8", newline="\n").write(original.replace(old, new, 1))
        code, summary, out = run(test_file)
        verdict = "变红(符合预期)" if code != 0 else "!! 仍然全绿（护栏无效）"
        print(f"[{verdict}] {name} -> {summary}")
        if code == 0:
            print("---- 输出尾部 ----")
            print("\n".join(out.strip().splitlines()[-15:]))
    finally:
        shutil.copyfile(path + ".bak", path)
        os.remove(path + ".bak")
        # 校验已还原
        assert open(path, encoding="utf-8").read() == original, f"还原失败: {path}"

print("done")
