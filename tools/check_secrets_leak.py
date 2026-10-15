"""CI guard：主仓不得跟踪 secrets/，不得含私有资产特征串。

退出码 0 = 干净；1 = 发现泄漏（stdout 列出问题）。完整特征串清单在
secrets/feature-strings.txt 维护，这里只放三条最强信号（措辞刻意、不易误伤）。
"""
import subprocess
import sys

MINIMAL_FEATURE_STRINGS = [
    "半结构式访谈方法论要点",
    "像一位有经验的导师在指导研究生",
    "覆盖不同立场、年龄与权力位置的访谈对象谱系",
]


def run_git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False).stdout


def main() -> int:
    problems = []
    tracked = [line for line in run_git("ls-files").splitlines()
               if line.startswith("secrets/")]
    if tracked:
        problems.append(f"secrets/ 下有被跟踪文件: {tracked}")
    for s in MINIMAL_FEATURE_STRINGS:
        # git grep 无匹配时 exit 1、stdout 空——只看 stdout 非空即命中。
        # pathspec 排除 docs/（计划与 spec 文档含特征串明文，其清洗由 M3 历史
        # 重写兜底）与本脚本自身（特征串字面量所在）——否则 guard 永远自命中。
        hits = run_git("grep", "-l", "--fixed-strings", s, "--",
                       ":(exclude)docs", ":(exclude)tools/check_secrets_leak.py").strip()
        if hits:
            problems.append(f"特征串 {s!r} 命中被跟踪文件:\n{hits}")
    if problems:
        print("SECRETS LEAK DETECTED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("secrets-leak guard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
