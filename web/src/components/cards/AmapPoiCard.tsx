import { CardShell, NoData, RawFields } from "./CardShell";
import { LIST_LIMIT, toItemPage } from "./data";
import { itemDigest } from "./fields";
import { toolLabel } from "../../lib/toolMeta";
import type { ToolCardProps } from "./types";

/**
 * AmapPoiCard — amap_text_search 列表简卡（spec C6）。
 * POI 结果可达 250 条，全量渲染刷屏；只显前 LIST_LIMIT 条 name + 语义化摘要，
 * 注明总数（红线：数字直出不加工，不臆造字段）。
 *
 * 摘要走 `fields.itemDigest`：`类型末级 · 地址 · 距离 · 营业时间`，不再把
 * `keytag`/`typecode` 之类原始键名打给用户；未识别字段进列表底部「更多原始字段」折叠。
 */
export function AmapPoiCard({ data, degraded }: ToolCardProps) {
  const page = toItemPage(data);
  const shown = page.items.slice(0, LIST_LIMIT);
  const rawLines = shown
    .map((it) => ({ name: it.name || "（未命名）", more: itemDigest(it).more }))
    .filter((row) => row.more.length > 0);
  return (
    <CardShell title={toolLabel("amap_text_search")} subtitle={`${page.total} 条`} degraded={degraded}>
      {page.total === 0 && shown.length === 0 ? (
        <NoData hint="本次地点检索未命中 POI。" />
      ) : (
        <>
          <ul className="space-y-1.5">
            {shown.map((it, i) => {
              const line = itemDigest(it).summary;
              return (
                <li key={`${it.name}-${i}`} className="text-[12.5px] leading-relaxed">
                  <span className="text-ink">{it.name || "（未命名）"}</span>
                  {line && <span className="text-inkfaint"> · {line}</span>}
                </li>
              );
            })}
          </ul>
          {page.total > LIST_LIMIT && (
            <p className="mt-1.5 text-[11px] text-inkfaint">共 {page.total} 条，此处仅列前 {LIST_LIMIT} 条。</p>
          )}
          {rawLines.length > 0 && <RawFields rows={rawLines} />}
        </>
      )}
    </CardShell>
  );
}
