"""documents/informants — 访谈对象候选的动态生成（suggest_informants）。

修订计划 Task 7+ 的内核化能力：候选生成**不限于示范库 11 类**——
- 有 llm：以 ``guidance.informants.INFORMANT_LIBRARY``（含 examples 提问示例）的 JSON 体
  作 few-shot 质量锚，user prompt 要求"输出 JSON 数组"，返回逐条
  ``InformantCandidate.model_validate`` 校验；非法条目丢弃、全部非法 →
  ``Envelope(ok=False, error.code="generation_invalid")``；
- 无 llm：``match_examples`` 关键词粗匹配降级，degraded 标注"参考起点，非完整覆盖"。

无状态：``llm`` 经 keyword-only 注入（spec 公理 2）；注册复用 proposal 槽
（documents 子包与宿主 LLM 配置同源，见 ``kernel/documents/proposal.py``）。
"""
import json
from typing import Any

from fieldwork_agent.kernel.guidance import informants as informant_lib
from fieldwork_agent.kernel.models import Envelope, InformantCandidate, ToolError
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from pydantic import BaseModel, ValidationError

# 候选数量上限（接口契约 data=1-6 类；prompt 要求 4-6 类，超限截断保底）
MAX_CANDIDATES = 6


class SuggestInformantsArgs(BaseModel):
    """``suggest_informants`` 的入参模型（``llm`` 注入参数不进 schema）。"""

    research_topic: str
    discipline: str = ""
    site_profile: dict[str, Any] | None = None


def _strip_code_fences(content: str) -> str:
    """容忍 ```json 代码围栏：真实模型常把数组包进围栏里返回。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        if text.endswith("```"):
            text = text.removesuffix("```")
    return text.strip()


def _user_prompt(research_topic: str, discipline: str, site_profile: dict | None) -> str:
    parts = [f"研究主题：{research_topic}", f"学科：{discipline or '未指定'}"]
    if site_profile:
        parts.append("田野点画像（供参考）：" + json.dumps(site_profile, ensure_ascii=False))
    parts.append(
        "示范词条（type/approach/cautions/question_domains/ethics/examples 的写法与"
        "颗粒度质量锚，仅示范写法、不是枚举白名单）：\n"
        + json.dumps(informant_lib.INFORMANT_LIBRARY, ensure_ascii=False)
    )
    parts.append(
        "请为该研究主题设计 4-6 类互不重复、覆盖不同立场与权力位置的访谈对象候选，"
        "每类给出 type、approach、cautions、question_domains（字符串列表）、ethics、"
        "access_channel、preparation 七个字段；"
        "对不易触达或敏感的人群，须显式写出接近方式与伦理安排。"
    )
    parts.append("输出 JSON 数组（顶层为数组，不要输出任何解释文字或代码围栏）。")
    return "\n\n".join(parts)


def _parse_candidates(content: str) -> list[InformantCandidate]:
    """解析并逐条校验：非法条目丢弃；type 为空的条目无信息量，同样丢弃。"""
    try:
        payload = json.loads(_strip_code_fences(content))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, list):
        return []
    out: list[InformantCandidate] = []
    for item in payload:
        try:
            candidate = InformantCandidate.model_validate(item)
        except ValidationError:
            continue  # 模型常夹带 1-2 条坏数据，逐条容错而非整批失败
        if candidate.type:
            out.append(candidate)
    return out[:MAX_CANDIDATES]


def suggest_informants(research_topic: str, discipline: str = "",
                       site_profile: dict | None = None, *,
                       llm: Any = None) -> Envelope:
    """动态生成访谈对象候选（data=``list[InformantCandidate]``，1-6 类）。"""
    if llm is None:
        data = [InformantCandidate.model_validate(d)
                for d in informant_lib.match_examples(research_topic)[:MAX_CANDIDATES]]
        return Envelope(ok=True, data=data, degraded="参考起点，非完整覆盖")
    messages = [
        {"role": "system",
         "content": "你是田野调查的信息提供者（报道人）设计助手：为研究主题铺设覆盖不同立场、"
                    "年龄与权力位置的访谈对象谱系，兼顾可触达性与伦理风险。"},
        {"role": "user", "content": _user_prompt(research_topic, discipline, site_profile)},
    ]
    resp = llm.chat(messages, slot="proposal", max_tokens=4096, temperature=0.5)
    candidates = _parse_candidates((resp or {}).get("content", "") or "")
    if not candidates:
        return Envelope(ok=False, error=ToolError(
            code="generation_invalid",
            message="模型返回未能解析出任何合法的访谈对象候选（期望 JSON 数组）",
            hint="可重试；或改走无 llm 降级路径获取示范库参考起点",
        ))
    return Envelope(ok=True, data=candidates)


register(CapabilityMeta(
    name="suggest_informants",
    description="为研究主题动态生成 1-6 类访谈对象候选（InformantCandidate）：有 llm 时以示范库"
                "（含提问示例 examples）JSON 体做 few-shot 质量锚并要求输出 JSON 数组，逐条 schema "
                "校验、非法条目丢弃、全非法报 generation_invalid；无 llm 时按关键词匹配示范库降级，"
                "degraded 标注“参考起点，非完整覆盖”。示范库不是枚举白名单。",
    fn=suggest_informants,
    args_model=SuggestInformantsArgs,
))
