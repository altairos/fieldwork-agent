"""研究上下文 patch 共享严格校验（审计二 R05 单一来源）。

Web 回合工具（``web_host/agent/state_tools.py``）、MCP
``update_research_context``、REST ``PUT /research-context`` 三入口统一走
``sanitize_context_patch``：白名单字段、类型严格、空白拒绝、
``interviews_not_applicable=True`` 需同时给理由（patch 内或 current 已有）。
任一字段非法即整次拒绝（原子语义），绝不部分写入。kernel 只依赖标准库。
"""

# title（任务1）：项目显示名——用户改名（REST）与 agent 自动命名共用此白名单入口
ALLOWED_CONTEXT_FIELDS = (
    "title",
    "discipline", "topic", "site_status", "scale", "budget",
    "time_window", "departure_city", "target_location", "travel_date",
    "purpose", "research_questions", "interviews_not_applicable",
    "interviews_na_reason",
)


def context_fields_hint() -> str:
    """白名单字段的提示文案（错误 hint 用），顿号分隔。"""
    return "、".join(ALLOWED_CONTEXT_FIELDS)


def sanitize_context_patch(patch: dict, current: dict | None = None) -> dict:
    """校验并净化研究上下文 patch；非法即 raise ValueError（文案含全部错误）。

    ``current`` 为当前已存上下文（仅用于 NA 理由的存量豁免）。
    """
    current = current or {}
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch 为空：至少一个白名单字段且值为非空字符串或布尔")
    errors = []
    for key, value in patch.items():
        if key not in ALLOWED_CONTEXT_FIELDS:
            errors.append(f"未知字段：{key}")
            continue
        if key == "interviews_not_applicable":
            if not isinstance(value, bool):
                errors.append(f"{key} 须为布尔")
            elif value and not str(
                patch.get("interviews_na_reason")
                or current.get("interviews_na_reason") or ""
            ).strip():
                errors.append(
                    "interviews_na_reason 需与 interviews_not_applicable=True "
                    "同时提供（存量已有理由则豁免）")
            continue
        if not isinstance(value, str):
            errors.append(f"{key} 须为字符串")
        elif not value.strip():
            errors.append(f"{key} 不得为空白字符串")
    if errors:
        raise ValueError("；".join(errors))
    return {k: (v if k == "interviews_not_applicable" else v.strip())
            for k, v in patch.items()}
