"""有限的研究场景检索矩阵；检索到地点并不证明研究价值或进入条件。"""
from typing import Annotated, Literal

from fieldwork_agent.kernel.models import Envelope, GeoLocation, ItemPage, ToolError
from fieldwork_agent.kernel.travel import visible_page
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class SpatialQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Term
    research_purpose: str = Field(min_length=1, max_length=300)
    place_types: list[Term] = Field(default_factory=list, max_length=4)
    keywords: list[Term] = Field(min_length=1, max_length=3)
    administrative_scope: str = Field(default="", max_length=150)
    search_anchor: Literal["site", "area"] = "site"
    radius_meters: int = Field(default=5000, ge=100, le=50000)
    expanded_radius_meters: int | None = Field(default=None, ge=100, le=50000)
    expected_evidence: str = Field(min_length=1, max_length=300)
    min_candidates: int = Field(default=2, ge=1, le=5)
    max_candidates: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.min_candidates > self.max_candidates:
            raise ValueError("min_candidates不得超过max_candidates")
        if self.expanded_radius_meters is not None and self.expanded_radius_meters <= self.radius_meters:
            raise ValueError("扩展半径须大于初始半径")
        return self


def execute_matrix(rows: list[SpatialQuery], geo: GeoLocation | None, location: str, client) -> list[dict]:
    reports = []
    for row in rows:
        report = {"query": row.model_dump(), "requests": [], "items": [],
                  "evidence_boundary": "候选观察场所；研究适切性、是否营业与进入条件待核实"}
        if row.search_anchor == "site" and (not geo or geo.ambiguous or not geo.location):
            report.update(status="unknown", stop_reason="需要唯一地理锚点，不以同名地名猜测")
            reports.append(report)
            continue
        items = []
        attempts = [(term, row.radius_meters) for term in row.keywords]
        if row.search_anchor == "site" and row.expanded_radius_meters and row.expanded_radius_meters > row.radius_meters:
            attempts.append((row.keywords[0], row.expanded_radius_meters))
        scope = row.administrative_scope or (geo.adcode if geo else "") or location
        for term, radius in attempts:
            try:
                env = (client.around_search(keywords=term, location=geo.location, radius=radius, page_size=row.max_candidates)
                       if row.search_anchor == "site" else client.text_search(keywords=term, city=scope,
                            page_size=row.max_candidates, city_limit=True))
            except Exception as exc:
                env = Envelope(ok=False, error=ToolError(code="source_exception", message=str(exc)))
            report["requests"].append({"keyword": term, "radius_meters": radius if row.search_anchor == "site" else None,
                "scope": scope, "ok": env.ok, "error": env.error.model_dump() if env.error else None,
                "evidence": env.evidence.model_dump() if env.evidence else None})
            if not env.ok:
                report["status"] = "error"
                if env.error and env.error.code == "source_budget_exceeded":
                    report.update(status="budget_exhausted", stop_reason="预算已用尽，保留已取得候选")
                    break
                if env.error and env.error.code in ("amap_ip_denied", "amap_invalid_key", "amap_quota"):
                    report.update(status="source_unavailable", stop_reason=env.error.hint)
                    break
                continue
            if isinstance(env.data, ItemPage):
                for item in env.data.items:
                    # 有返回行政区时复核；无字段的结果保留未知，不冒充已核验范围。
                    code = str(item.extra.get("adcode") or "")
                    if scope.isdigit() and code and not code.startswith(scope.rstrip("0")):
                        continue
                    copied = item.model_copy(deep=True)
                    copied.extra.update(research_purpose=row.research_purpose, matched_queries=[row.label],
                        administrative_scope_verified=bool(scope.isdigit() and code))
                    items.append(copied)
            page = visible_page(items, row.max_candidates)
            report["items"] = [item.model_dump() for item in page.items]
            if len(page.items) >= row.min_candidates:
                report.update(status="enough", stop_reason="已达到该路线候选数量，停止扩词扩范围")
                break
        else:
            report.update(status="narrow" if report["items"] else "empty", stop_reason="有限词表与范围已检索完毕")
        reports.append(report)
    return reports
