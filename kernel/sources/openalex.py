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
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Any, Literal

from fieldwork_agent.kernel.models import (
    Envelope,
    LiteratureItem,
    LiteraturePage,
    LiteratureQueryReport,
    LiteratureSourceLink,
    ToolError,
)
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator

BASE_URL = "https://api.openalex.org"
_TIMEOUT = 15  # 学术 API 比高德 REST 略慢，给到 15s
_UA = "fieldwork-agent/1.0 (https://github.com/fieldwork-agent; scholarly search)"
MAX_MATRIX_ROWS = 4
MAX_LITERATURE_RESULTS = 20
_PROBE_SIZE = 4
_FOCUSED_SIZE = 8

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
    "invalid_arguments": "请检查非空检索词、年份范围与矩阵大小；不要提交超长检索式。",
}

SearchTerm = Annotated[str, StringConstraints(
    strip_whitespace=True, min_length=1, max_length=80, pattern=r'^[^"\\*?()\r\n]+$')]
TermGroup = Annotated[list[SearchTerm], Field(min_length=1, max_length=4)]


class LiteratureQuery(BaseModel):
    """agent 从研究问题生成一行矩阵：组内 OR、组间 AND，词项为字面短语。"""

    label: str = Field(min_length=1, max_length=40, description="检索目的短标签")
    purpose: Literal["core", "theory", "method", "context"] = "core"
    groups: list[TermGroup] = Field(
        min_length=1, max_length=3,
        description="每组是同一概念的1-4个英文同义词/短语，组内OR、组间AND；不要穷举笛卡尔积")
    exclude_terms: list[SearchTerm] = Field(
        default_factory=list, max_length=3, description="只排除已确认的歧义词，避免误删相关研究")

    def expression(self) -> str:
        parts = ["(" + " OR ".join(f'"{t}"' for t in dict.fromkeys(g)) + ")"
                 for g in self.groups]
        expression = " AND ".join(parts)
        if self.exclude_terms:
            expression += " NOT (" + " OR ".join(f'"{t}"' for t in self.exclude_terms) + ")"
        return expression


class SearchLiteratureArgs(BaseModel):
    """单一 schema：Web/CLI/MCP 及客户端直调共享参数校验。"""

    query: str = Field(
        min_length=1, max_length=600,
        description="原研究主题/本次检索目标；无矩阵时作为英文布尔检索式，不能为空")
    year_from: int | None = Field(default=None, ge=1000, le=2200)
    year_to: int | None = Field(default=None, ge=1000, le=2200)
    max_results: int = Field(
        default=8, ge=1, le=MAX_LITERATURE_RESULTS,
        description="本次全部矩阵路线合计返回的候选上限（1-20），不是每路条数")
    open_access_only: bool = Field(default=False, description="仅用户明确要求免费全文时启用")
    discipline: str = Field(
        default="", max_length=80, description="学科背景；不直接追加到检索式，相关英文概念写入矩阵")
    search_matrix: list[LiteratureQuery] = Field(
        default_factory=list, max_length=MAX_MATRIX_ROWS,
        description="先根据用户研究问题生成2-4路矩阵：核心、理论、方法或情境；已知题名核验可省略")
    stage: Literal["probe", "focused"] = Field(
        default="probe", description="先probe试检（每路最多4篇），根据query_reports调整后focused（每路最多8篇）")
    search_field: Literal["title_and_abstract", "title", "all"] = Field(
        default="title_and_abstract", description="默认仅标题摘要；核验题名用title，明确放宽时才用all")

    @model_validator(mode="after")
    def check_scope(self):
        self.query = self.query.strip()
        if not self.query:
            raise ValueError("检索主题不能为空")
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("起始年不能晚于截止年")
        labels = [row.label for row in self.search_matrix]
        if len(set(labels)) != len(labels):
            raise ValueError("矩阵路线标签不能重复")
        return self


def literature_identity_keys(item: LiteratureItem) -> set[str]:
    """DOI/OpenAlex ID 双重去重；无标识时才回退到规范化题名+年份。"""
    keys = set()
    if item.doi:
        keys.add("doi:" + _strip_doi(item.doi).strip().casefold())
    if item.id:
        keys.add("id:" + item.id.rstrip("/").casefold())
    if not keys and item.title:
        title = re.sub(r"[\W_]+", "", item.title.casefold())
        keys.add(f"title:{title}:{item.year}")
    return keys


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
    text = str(doi or "").strip()
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.I)


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
                     year_to: int | None = None, max_results: int = 8,
                     open_access_only: bool = False, discipline: str = "",
                     search_matrix: list[dict | LiteratureQuery] | None = None,
                     stage: str = "probe", search_field: str = "title_and_abstract") -> Envelope:
        """执行有界矩阵，禁止翻页；每路试检后由宿主 agent 决定下一轮改写。"""
        try:
            args = SearchLiteratureArgs(
                query=query, year_from=year_from, year_to=year_to, max_results=max_results,
                open_access_only=open_access_only, discipline=discipline,
                search_matrix=search_matrix or [], stage=stage, search_field=search_field)
        except ValidationError as exc:
            return _error_envelope(str(exc), "invalid_arguments")
        routes = [(row.label, row.purpose, row.expression()) for row in args.search_matrix]
        if not routes:
            routes = [("直接检索", "core", args.query)]
        # 相同表达式只请求一次；先按全部实际 URL 做预检，超长参数不触达网络。
        routes = list({expression: (label, purpose, expression)
                       for label, purpose, expression in routes}.values())
        search_key = {"title_and_abstract": "search.title_and_abstract",
                      "title": "search.title", "all": "search"}[args.search_field]
        params: dict[str, Any] = {
            "per-page": min(args.max_results, _PROBE_SIZE if args.stage == "probe" else _FOCUSED_SIZE),
            "sort": "relevance_score:desc",
        }
        filters: list[str] = []
        if args.year_from or args.year_to:
            filters.append(f"publication_year:{args.year_from or ''}-{args.year_to or ''}")
        if args.open_access_only:
            filters.append("is_oa:true")
        if filters:
            params["filter"] = ",".join(filters)
        if self.email:
            params["mailto"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key

        requests = [(label, purpose, expression, {**params, search_key: expression})
                    for label, purpose, expression in routes]
        if any(len(f"{BASE_URL}/works?{urllib.parse.urlencode(p)}".encode()) > 4000
               for _, _, _, p in requests):
            return _error_envelope("检索 URL 过长，请减少每组同义词或拆分矩阵", "invalid_arguments")

        reports: list[LiteratureQueryReport] = []
        batches: list[list[LiteratureItem]] = []
        warnings = []
        for label, purpose, expression, request_params in requests:
            payload = self._get("/works", request_params)
            report = LiteratureQueryReport(
                label=label, purpose=purpose, query=expression, search_field=args.search_field)
            reports.append(report)
            if "_local_error" in payload:
                report.error = _error_envelope(str(payload["_local_error"])).error
                report.assessment = "error"
                report.next_action = "保留其它路线结果；按错误提示处理，不把失败当作零命中。"
                warnings.append(f"{label}：{report.error.code}")
                # 配额/超时是服务级故障，停止后续请求，避免四路各等15秒。
                if report.error.code in {"literature_rate_limited", "openalex_timeout"}:
                    break
                continue
            results = payload.get("results") or []
            batch = [_to_item(r) for r in results[:params["per-page"]] if isinstance(r, dict)]
            for item in batch:
                item.matched_queries = [label]
            batches.append(batch)
            count = (payload.get("meta") or {}).get("count")
            report.matched_total = int(count) if isinstance(count, (int, float)) else None
            report.retrieved_count = len(batch)
            n = report.matched_total
            if n is None:
                report.assessment, report.next_action = "unknown", "总命中数缺失；只依据实际取回的标题摘要筛选。"
            elif n == 0:
                report.assessment, report.next_action = "empty", "换同义词或放宽一个非核心概念；保留研究问题锚点。"
            elif n < 3:
                report.assessment, report.next_action = "narrow", "先核验现有条目；必要时去掉地点/方法限制，单独补检。"
            elif n > 500:
                report.assessment, report.next_action = "broad", "增加对象/机制锚点或收窄字段；不要翻页或增加条数。"
            else:
                report.assessment, report.next_action = "screen", "依据标题摘要筛选；已覆盖问题就停止，勿为凑数继续检索。"

        # 轮流取各路排名靠前的候选，保留理论/方法路线；不跨查询比较 relevance_score。
        unique: list[LiteratureItem] = []
        aliases: dict[str, LiteratureItem] = {}
        for rank in range(max((len(b) for b in batches), default=0)):
            for batch in batches:
                if rank >= len(batch):
                    continue
                item = batch[rank]
                keys = literature_identity_keys(item)
                previous = next((aliases[k] for k in keys if k in aliases), None)
                if previous is not None:
                    previous.matched_queries = list(dict.fromkeys(
                        previous.matched_queries + item.matched_queries))
                    # 同一论文的较完整记录可补齐初筛证据，不能因先见到无摘要版本而丢掉摘要。
                    previous.abstract = previous.abstract or item.abstract
                    previous.authors = previous.authors or item.authors
                    previous.doi = previous.doi or item.doi
                    previous.venue = previous.venue or item.venue
                    for key in keys:
                        aliases[key] = previous
                    continue
                unique.append(item)
                for key in keys:
                    aliases[key] = item
        items = unique[:args.max_results]
        retrieved = sum(len(b) for b in batches)
        if len(reports) < len(requests):
            warnings.append(f"服务异常后停止；{len(requests) - len(reports)} 路未执行。")
        page = LiteraturePage(
            items=items, total=len(items), returned_count=len(items),
            retrieved_count=retrieved, unique_count=len(unique),
            duplicates_removed=retrieved - len(unique),
            matched_total=reports[0].matched_total if len(requests) == 1 else None,
            query_reports=reports, truncated=len(unique) > len(items), warnings=warnings,
            chinese_sources=_chinese_source_links(args.query))
        if items:
            return Envelope(ok=True, data=page,
                            degraded="partial_literature_search" if warnings else None)
        error = next((r.error for r in reports if r.error), None)
        return Envelope(ok=False, data=page, error=error or _no_result_envelope().error)


# ════════════════════════════════════════════════════════════════════
# 能力注册 —— search_literature 进 registry（schema 单一来源，与 amap 同一模式）
# ════════════════════════════════════════════════════════════════════


def search_literature(query: str, year_from: int | None = None,
                      year_to: int | None = None, max_results: int = 8,
                      open_access_only: bool = False, discipline: str = "",
                      search_matrix: list[dict | LiteratureQuery] | None = None,
                      stage: str = "probe", search_field: str = "title_and_abstract", *,
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
                               discipline=discipline, search_matrix=search_matrix,
                               stage=stage, search_field=search_field)


register(CapabilityMeta(
    name="search_literature",
    description="OpenAlex 文献检索：先将用户研究问题拆为对象、现象、情境、理论或方法，生成2-4路"
                "search_matrix（同义词OR、概念组AND）；先probe，根据query_reports收窄或放宽一个条件后focused。"
                "默认只搜标题摘要，无自动翻页，合计最多20篇候选；total是实际返回数，matched_total才是数据库"
                "命中数，多路命中不可相加。仅凭返回的标题摘要推荐5-8篇，说明用途与局限，不宣称已读全文。"
                "中文期刊用返回的平台入口补检，不臆造引用。",
    fn=search_literature, args_model=SearchLiteratureArgs,
    read_only=True, open_world=True))
