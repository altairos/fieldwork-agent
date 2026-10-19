import { asRecord, numOf, strOf } from "./data";

/** 只展示实际返回字段；时间、枢纽及缓存日期不从名称猜测。 */
export function travelDetails(raw: unknown): string {
  const item = asRecord(raw);
  const s = asRecord(item.schedule);
  const e = asRecord(item.evidence);
  const duration = numOf(s.duration_minutes);
  const parts = [
    [strOf(s.dep_date), strOf(s.dep_time), strOf(s.departure_place)].filter(Boolean).join(" "),
    [strOf(s.arr_date), strOf(s.arr_time), strOf(s.arrival_place)].filter(Boolean).join(" "),
  ].filter(Boolean);
  const timing = parts.join(" → ");
  const status = strOf(asRecord(item.constraints).status);
  return [timing, duration === null ? "" : `${duration} 分钟`,
    status === "unknown" ? "接续条件待核实" : status === "failed" ? "不满足出行条件" : "",
    strOf(e.fetched_at) ? `${e.cache_hit ? "缓存采样" : "采样"} ${strOf(e.fetched_at)}` : "",
  ].filter(Boolean).join(" · ");
}
