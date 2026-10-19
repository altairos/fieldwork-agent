import { Price } from "../ui/Price";
import { asArray, asRecord, numOf, strOf } from "./data";

export function LodgingComparison({ data }: { data: unknown }) {
  const rows = asArray(data);
  if (!rows.length) return null;
  return <section aria-label="住宿与通勤比较" className="space-y-2">
    <div className="text-[12px] font-medium">住宿与通勤比较</div>
    {rows.slice(0, 3).map((raw, index) => {
      const row = asRecord(raw);
      const c = asRecord(row.constraints);
      const minutes = numOf(row.commute_minutes_per_visit_day);
      const research = numOf(row.research_minutes_per_visit_day);
      return <div key={`${strOf(row.name)}-${index}`} className="rounded-md border border-hairline p-2 text-[11px]">
        <p>{strOf(row.name)} · {strOf(c.status) === "failed" ? "不满足约束" : "条件待核实"}</p>
        <p>房费与通勤合计情景估算：<Price value={numOf(row.cost_estimate)} source="estimate" /></p>
        <p>{minutes === null ? "每日往返时长未知" : `每日往返约 ${minutes} 分钟`}
          {research === null ? "" : ` · 按所给日安排余 ${research} 分钟`}</p>
        <p className="text-inkfaint">{strOf(row.price_basis_assumption)}</p>
        <p className="text-ochre">{asArray(c.failures).concat(asArray(c.missing_fields)).map(v => strOf(v)).filter(Boolean).join("；")}</p>
      </div>;
    })}
  </section>;
}
