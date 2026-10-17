import { CardShell, NoData, Notice } from "./CardShell";
import type { ToolCardProps } from "./types";

interface WindowItem {
  start: string; end: string; score: number;
  reasons?: string[] | null; evidence?: Record<string, unknown> | null;
}
interface Curve {
  dates?: string[] | null;
  flight_min?: Array<number | null> | null;
  hotel_min?: Array<number | null> | null;
}
interface Festival {
  name?: string; approx_dates?: string;
  approx_start?: string; approx_end?: string; relevance?: string;
}
interface Analysis {
  windows?: WindowItem[] | null;
  price_curve?: Curve | null;
  festivals?: Festival[] | null;
  budget_feasible?: boolean | null;
  degraded?: string | null;
}

/** 价格序列 → polyline 点串：null 采样点跳过（x 仍按日期索引等距），值域归一化。 */
function points(series: Array<number | null>, w: number, h: number, pad = 10): string {
  const pts = series
    .map((v, i) => (v == null ? null : { i, v }))
    .filter((p): p is { i: number; v: number } => p !== null);
  if (pts.length < 2) return "";
  const lo = Math.min(...pts.map((p) => p.v));
  const hi = Math.max(...pts.map((p) => p.v));
  const span = hi - lo || 1;
  const step = w / Math.max(1, series.length - 1);
  return pts
    .map(({ i, v }) =>
      `${(i * step).toFixed(1)},${(pad + (1 - (v - lo) / span) * (h - 2 * pad)).toFixed(1)}`)
    .join(" ");
}

export function WindowCard({ data, degraded }: ToolCardProps) {
  const a = (data ?? null) as Analysis | null;
  if (!a) return <NoData />;
  const curve = a.price_curve ?? {};
  const dates = curve.dates ?? [];
  const flightPts = points(curve.flight_min ?? [], 320, 96);
  const hotelPts = points(curve.hotel_min ?? [], 320, 96);
  return (
    <CardShell
      title="调研时窗分析"
      subtitle={dates.length >= 2
        ? `${dates[0]} → ${dates[dates.length - 1]} · 采样 ${dates.length} 点` : undefined}
      statusChip={a.budget_feasible == null
        ? <span className="text-[11px] text-ochre">待核验（仅已知小计）</span>
        : <span className={`text-[11px] ${a.budget_feasible ? "text-bamboo" : "text-danger"}`}>
            {a.budget_feasible ? "预算内" : "超预算"}
          </span>}
      degraded={degraded}
    >
      {a.degraded ? <Notice tone="ochre">{a.degraded}</Notice> : null}
      {(a.windows ?? []).slice(0, 3).map((w, i) => (
        <div key={i} className="mb-2 last:mb-0 rounded-md border border-hairline bg-cardsunk/40 p-2.5">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[12.5px] font-medium text-ink">
              推荐 {i + 1}：{w.start} → {w.end}
            </span>
            <span className="text-[11.5px] text-ochre shrink-0">{w.score.toFixed(1)} 分</span>
          </div>
          <ul className="mt-1 space-y-0.5">
            {(w.reasons ?? []).slice(0, 3).map((r, j) => (
              <li key={j} className="text-[12px] text-inksoft">· {r}</li>
            ))}
          </ul>
        </div>
      ))}
      {flightPts || hotelPts ? (
        <figure className="mt-2">
          <svg viewBox="0 0 320 96" className="w-full h-24" role="img" aria-label="多日期价格采样曲线">
            {hotelPts ? <polyline fill="none" stroke="#b45309" strokeWidth="2" points={hotelPts} /> : null}
            {flightPts ? <polyline fill="none" stroke="#0e7490" strokeWidth="2" points={flightPts} /> : null}
          </svg>
          <figcaption className="text-[11px] text-inkfaint">
            <span className="text-[#0e7490]">━</span> 航班最低价　<span className="text-[#b45309]">━</span> 住宿最低价（每晚）
          </figcaption>
        </figure>
      ) : null}
      {(a.festivals ?? []).length > 0 ? (
        <div className="mt-2 space-y-1">
          {(a.festivals ?? []).map((f, i) => (
            <div key={i} className="text-[12px] text-inksoft">
              <span className="text-ink">{f.name}</span>
              {f.approx_dates ? ` · ${f.approx_dates}` : ""}
              {f.relevance ? ` — ${f.relevance}` : ""}
            </div>
          ))}
        </div>
      ) : null}
    </CardShell>
  );
}
