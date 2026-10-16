# 一次性补丁脚本（终审 I2）：proposal_handler 无 llm 成功路径补 degraded 键；用后即弃，不入 git
from pathlib import Path

p = Path(r"f:\Agents_dev\Fieldwork_Agent\kernel\documents\proposal.py")
src = p.read_text(encoding="utf-8")

old_doc = '''    """registry 分派入口：宿主以 JSON 传 ``ctx`` dict，内核转 ResearchContext 后统一包络。"""'''
new_doc = '''    """registry 分派入口：宿主以 JSON 传 ``ctx`` dict，内核转 ResearchContext 后统一包络。

    终审 I2（验收③）：``llm is None`` 且 compose 成功时，包络带非空 ``degraded``，
    使"模板降级"对机器可见（此前只在 markdown 尾注里，人眼可见、程序不可见）。
    """'''

old_ret = '''            hint="先补齐研究者信息/田野点/行程/访谈对象四渠道，再生成方案"))
    return Envelope(ok=True, data=doc)'''
new_ret = '''            hint="先补齐研究者信息/田野点/行程/访谈对象四渠道，再生成方案"))
    if llm is None:
        # 措辞对齐 informants.suggest_informants 的既有 degraded 风格（"参考起点，非完整覆盖"）；
        # llm 非 None 路径不动（chat error 分支的内部降级仍由 compose_proposal 尾注承担）
        return Envelope(ok=True, data=doc, degraded="模板降级版（未接入 LLM）")
    return Envelope(ok=True, data=doc)'''

assert src.count(old_doc) == 1, "docstring 锚点不唯一"
assert src.count(old_ret) == 1, "return 锚点不唯一"
src = src.replace(old_doc, new_doc).replace(old_ret, new_ret)
p.write_text(src, encoding="utf-8")
print("patched OK")
