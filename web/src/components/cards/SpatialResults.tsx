import { asArray, asRecord, strOf } from "./data";

export function SpatialResults({ data }: { data: unknown }) {
  const rows = asArray(data);
  if (!rows.length) return null;
  return <section aria-label="候选观察场所" className="space-y-2">
    <div className="text-[12px] font-medium">候选观察场所</div>
    {rows.slice(0, 4).map((raw, index) => {
      const row = asRecord(raw);
      const query = asRecord(row.query);
      const items = asArray(row.items);
      return <div key={`${strOf(query.label)}-${index}`} className="border border-hairline rounded-md p-2 text-[11px]">
        <p>{strOf(query.label)} · {strOf(query.research_purpose)}</p>
        <p>{items.length ? items.map(item => strOf(asRecord(item).name)).filter(Boolean).join("、") : "本路尚无可用候选"}</p>
        <p className="text-inkfaint">{strOf(row.evidence_boundary)}</p>
        <details><summary className="cursor-pointer text-inkfaint">检索范围与停止原因</summary>
          <p>{strOf(query.administrative_scope)} · {asArray(query.keywords).map(v => strOf(v)).join(" / ")}</p>
          <p>{strOf(row.stop_reason)}</p>
        </details>
      </div>;
    })}
  </section>;
}
