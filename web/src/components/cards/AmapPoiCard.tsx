import { CardShell, NoData } from "./CardShell";
import { LIST_LIMIT, extraLine, toItemPage } from "./data";
import { toolLabel } from "./toolLabels";
import type { ToolCardProps } from "./types";

/**
 * AmapPoiCard — amap_text_search 列表简卡（spec C6）。
 * POI 结果可达 250 条，全量渲染刷屏；只显前 LIST_LIMIT 条 name+extra 摘要，
 * 注明总数（红线：数字直出不加工，不臆造字段——extra 里有什么显什么）。
 */
export function AmapPoiCard({ data, degraded }: ToolCardProps) {
  const page = toItemPage(data);
  const shown = page.items.slice(0, LIST_LIMIT);
  return (
    <CardShell title={toolLabel("amap_text_search")} subtitle={`${page.total} 条`} degraded={degraded}>
      {page.total === 0 && shown.length === 0 ? (
        <NoData hint="本次地点检索未命中 POI。" />
      ) : (
        <>
          <ul className="space-y-1.5">
            {shown.map((it, i) => {
              const line = extraLine(it.extra);
              return (
                <li key={`${it.name}-${i}`} className="text-[12.5px] leading-relaxed">
                  <span className="text-ink">{it.name || "（未命名）"}</span>
                  {line && <span className="text-inkfaint"> · {line}</span>}
                </li>
              );
            })}
          </ul>
          {page.total > LIST_LIMIT && (
            <p className="mt-2 text-[11.5px] text-inkfaint">共 {page.total} 条，此处仅列前 {LIST_LIMIT} 条。</p>
          )}
        </>
      )}
    </CardShell>
  );
}
