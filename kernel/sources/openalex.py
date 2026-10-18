"""OpenAlex 学术文献检索 — 社科论文库开放接口（问题2：旅行类工具不覆盖文献）。

与 FlyAI/高德的分工：FlyAI 强在旅行供给、高德强在地理空间底座，二者都不覆盖学术
文献（生产会话实测 search_keyword 返回酒店促销、search_ai 返回 0 条）。OpenAlex 是
免费开放的学术图谱（2.5 亿+ 文献，CC0 许可），REST API 无需 key 即可基础调用
（配 ``OPENALEX_EMAIL`` 进 polite pool 更稳、``OPENALEX_API_KEY`` 提每日预算），
补人类学/社会学等社科主题的文献检索缺口。

传输层用 urllib（kernel 只依赖标准库 + pydantic 的约束不破，同 sources/amap.py）；
任何失败都不抛异常，统一折 ``Envelope(ok=False, error=ToolError(...))``。摘要以
inverted index 存储（词→位置列表），需 :func:`reconstruct_abstract` 重建为纯文本。

中文社科补位（B2 spike 实证 2026-10-19）：OpenAlex 以外文为主，中文期刊覆盖弱。
ncpssd（国家哲社文献中心）无 GET 检索深链（AJAX+登录，候选 URL 均 404），仅主页可达；
PubScholar（中科院公益学术平台、免登录、ncpssd 主页自推的外部资源）``/search?q=`` 为
SPA 路由、浏览器打开即渲染结果。故 :func:`_chinese_source_links` 生成“平台入口 + 带
query 深链”两类可点击链接，随包络返回，由助手如实呈现（不臆造中文全文命中）。
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fieldwork_agent.kernel.models import (
    Envelope,
    LiteratureItem,
    LiteraturePage,
    LiteratureSourceLink,
    ToolError,
)
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from pydantic import BaseModel, Field

BASE_URL = "https://api.openalex.org"
_TIMEOUT = 15  # 学术 API 比高德 REST 略慢，给到 15s
_UA = "fieldwork-agent/1.0 (https://github.com/fieldwork-agent; scholarly search)"
_PER_PAGE_CAP = 50  # OpenAlex per-page 上限 200，工具结果卡取 50 足够

# 中文社科平台入口（B2 spike 实证可达；ncpssd 需注册登录、无 query 深链故给主页）
_NCPSSD_HOME = "https://www.ncpssd.cn/"
_PUBSCHOLAR_SEARCH = "https://pubscholar.cn/search?q="

_HINTS = {
    "openalex_timeout": "OpenAlex 接口超时：请稍后重试，或缩小检索范围（加年份/开放获取过滤）。",
    "literature_rate_limited": (
        "OpenAlex 触发限流（HTTP 429）：免费档无 key 每日预算有限，"
        "可配置 OPENALEX_EMAIL 进 polite pool、或 OPENALEX_API_KEY 提额后重试。"),
    "openalex_no_result": (
        "OpenAlex 未命中该检索：可换更宽泛的英文关键词（OpenAlex 以外文为主），"
        "或用返回的中文平台深链（国家哲社文献中心 / PubScholar）检索中文期刊。"),
    "parse_error": "OpenAlex 返回了非 JSON 内容：请重试；持续出现请查看 https://openalex.org 状态。",
    "openalex_error": "OpenAlex 服务异常：请稍后重试，或检查 https://api.openalex.org 可达性。",
}


def _error_envelope(message: str, code: str = "") -> Envelope:
    """本地失败（传输/解析/HTTP）按内容归类——与 amap._error_envelope 同一模式。"""
    low = message.lower()
    if not code:
        if "timeout" in low or "timed out" in low:
            code = "openalex_timeout"
        elif "429" in message:
            code = "literature_rate_limited"
        elif "json" in low:
            code = "parse_error"
        else:
            code = "openalex_error"
    return Envelope(ok=False, error=ToolError(
        code=code, message=message or "OpenAlex 调用失败",
        hint=_HINTS.get(code, _HINTS["openalex_error"])))


def _no_result_envelope() -> Envelope:
    return Envelope(ok=False, error=ToolError(
        code="openalex_no_result", message="OpenAlex 未命中任何文献",
        hint=_HINTS["openalex_no_result"]))


def reconstruct_abstract(inv: Any) -> str:
    """OpenAlex 摘要 inverted index（``{词: [位置…]}``）→ 按位置重排的纯文本。

    非 dict / 空 → 空串（无摘要不臆造）。位置去重后升序拼接，还原原始语序。
    """
    if not isinstance(inv, dict) or not inv:
        return ""
    placed: list[tuple[int, str]] = []
    for word, positions in inv.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int):
                placed.append((pos, str(word)))
    placed.sort(key=lambda t: t[0])
    return " ".join(w for _, w in placed)


def _strip_doi(doi: Any) -> str:
    """OpenAlex doi 带 ``https://doi.org/`` 前缀：剥成裸 DOI（前端自拼链接）。"""
    text = str(doi or "")
    return text.removeprefix("https://doi.org/").removeprefix("http://doi.org/")


def _to_item(r: dict[str, Any]) -> LiteratureItem:
    """单条 work → LiteratureItem（字段名逐字对齐 OpenAlex schema，缺失给空值不臆造）。"""
    authors = [str((a.get("author") or {}).get("display_name") or "")
               for a in (r.get("authorships") or [])]
    authors = [a for a in authors if a]
    loc = r.get("primary_location") or {}
    src = loc.get("source") or {}
    oa = r.get("open_access") or {}
    year = r.get("publication_year")
    return LiteratureItem(
        id=str(r.get("id") or ""),
        title=str(r.get("title") or r.get("display_name") or ""),
        authors=authors,
        year=int(year) if isinstance(year, (int, float)) else None,
        doi=_strip_doi(r.get("doi")),
        venue=str(src.get("display_name") or ""),
        cited_by_count=int(r.get("cited_by_count") or 0),
        abstract=reconstruct_abstract(r.get("abstract_inverted_index")),
        open_access_url=oa.get("oa_url"),
        is_oa=bool(oa.get("is_oa")),
    )


def _chinese_source_links(query: str) -> list[LiteratureSourceLink]:
    """中文社科检索深链：ncpssd 平台入口 + PubScholar 带 query 深链（B2 兜底）。

    ncpssd 无 GET 检索深链（spike 实证 404、需登录），给主页入口并如实标注；
    PubScholar 免登录、``/search?q=`` 可用，把 query URL 编码后拼进深链。
    """
    encoded = urllib.parse.quote(query, safe="")
    return [
        LiteratureSourceLink(
            label="国家哲学社会科学文献中心（中文社科期刊全文免费，需注册登录后检索）",
            url=_NCPSSD_HOME),
        LiteratureSourceLink(
            label="PubScholar 公益学术平台（中科院主办，免登录）",
            url=f"{_PUBSCHOLAR_SEARCH}{encoded}"),
    ]


class OpenAlexClient:
    """OpenAlex REST 客户端（自持出网，无 key 也可基础调用；同 amap 自持模式）。

    ``email`` 进 polite pool（OpenAlex 推荐的稳定调用方式），``api_key`` 提每日预算；
    二者缺省从环境变量 ``OPENALEX_EMAIL`` / ``OPENALEX_API_KEY`` 读取，空值不影响出网。
    """

    def __init__(self, email: str = "", api_key: str = ""):
        self.email = email or os.getenv("OPENALEX_EMAIL", "")
        self.api_key = api_key or os.getenv("OPENALEX_API_KEY", "")

    # ══════════════════════════════════════════
    # 传输层
    # ══════════════════════════════════════════

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET 一枚接口并解析 JSON；本地失败以 ``{"_local_error": ...}`` 上报
        （与 amap 的 ``{"infocode": "00000"}`` 同一降级约定，不向上抛异常）。"""
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
        except TimeoutError:
            return {"_local_error": "timeout: timed out"}
        except json.JSONDecodeError as e:
            return {"_local_error": f"JSON parse error: {e}"}
        except urllib.error.HTTPError as e:
            return {"_local_error": f"HTTP {e.code}"}
        except (urllib.error.URLError, OSError) as e:
            return {"_local_error": f"urlopen error: {e}"}

    # ══════════════════════════════════════════
    # 文献检索
    # ══════════════════════════════════════════

    def search_works(self, query: str, year_from: int | None = None,
                     year_to: int | None = None, max_results: int = 10,
                     open_access_only: bool = False, discipline: str = "") -> Envelope:
        """按关键词检索学术文献 → ``Envelope(data=LiteraturePage)``。

        ``discipline`` 非空时作为软相关信号追加进检索词（OpenAlex relevance search
        跨标题/摘要/全文）；年份与开放获取折进 ``filter``；附中文社科深链兜底。
        """
        search_terms = f"{query} {discipline}".strip() if discipline else query
        params: dict[str, Any] = {
            "search": search_terms,
            "per-page": min(max(1, max_results), _PER_PAGE_CAP),
            "sort": "relevance_score:desc",
        }
        filters: list[str] = []
        if year_from or year_to:
            filters.append(f"publication_year:{year_from or ''}-{year_to or ''}")
        if open_access_only:
            filters.append("is_oa:true")
        if filters:
            params["filter"] = ",".join(filters)
        if self.email:
            params["mailto"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key

        payload = self._get("/works", params)
        if "_local_error" in payload:
            return _error_envelope(str(payload["_local_error"]))
        results = payload.get("results") or []
        chinese = _chinese_source_links(query)
        if not results:
            # 无命中也带中文深链：助手可引导用户转中文平台检索（不空手而归）
            return Envelope(ok=False, error=ToolError(
                code="openalex_no_result", message="OpenAlex 未命中任何文献",
                hint=_HINTS["openalex_no_result"]),
                data=LiteraturePage(items=[], total=0, chinese_sources=chinese))
        items = [_to_item(r) for r in results]
        total = (payload.get("meta") or {}).get("count")
        return Envelope(ok=True, data=LiteraturePage(
            items=items, total=int(total) if isinstance(total, (int, float)) else len(items),
            chinese_sources=chinese))


# ════════════════════════════════════════════════════════════════════
# 能力注册 —— search_literature 进 registry（schema 单一来源，与 amap 同一模式）
# ════════════════════════════════════════════════════════════════════


def search_literature(query: str, year_from: int | None = None,
                      year_to: int | None = None, max_results: int = 10,
                      open_access_only: bool = False, discipline: str = "", *,
                      openalex_client: OpenAlexClient | None = None) -> Envelope:
    """文献检索能力 fn：注入 ``openalex_client`` 用宿主实例，未注入自建 ``OpenAlexClient()``。

    形参名刻意用 ``openalex_client`` 而非 ``client``：后者是宿主 dispatch 的 FlyAIClient
    保留键（mcp_server/dispatch.py RESERVED_KEYS / web_host dispatch / orchestrator 三处），
    同名会被注入 FlyAIClient 造成串味（AttributeError 假 internal_error）。非保留键名
    宿主不注入，fn 走自建默认（同 estimate_living_cost 的 inject="" 自持模式）。
    """
    target = openalex_client if openalex_client is not None else OpenAlexClient()
    return target.search_works(query, year_from=year_from, year_to=year_to,
                               max_results=max_results, open_access_only=open_access_only,
                               discipline=discipline)


class SearchLiteratureArgs(BaseModel):
    """``search_literature`` 入参（``client`` 注入参数不进 schema）。"""

    query: str = Field(description="检索词（建议英文主题词，OpenAlex 以外文为主；中文主题另用返回的中文平台深链）")
    year_from: int | None = Field(default=None, description="起始出版年（含），如 2000")
    year_to: int | None = Field(default=None, description="截止出版年（含），如 2024")
    max_results: int = Field(default=10, description="返回条数上限（1-50）")
    open_access_only: bool = Field(default=False, description="仅开放获取（可免费看全文）")
    discipline: str = Field(default="", description="学科软信号（如 人类学/社会学），追加进检索词")


register(CapabilityMeta(
    name="search_literature",
    description="学术文献检索（OpenAlex 开放学术图谱，2.5 亿+ 文献）：按关键词/年份/开放获取检索"
                "论文元数据（标题/作者/年份/期刊/被引/DOI/摘要/OA 全文链接），补旅行类工具不覆盖的"
                "文献缺口；以外文为主，返回附国家哲社文献中心与 PubScholar 中文社科检索深链。"
                "不臆造引用：只呈现检索到的真实条目。",
    fn=search_literature, args_model=SearchLiteratureArgs,
    read_only=True, open_world=True))
