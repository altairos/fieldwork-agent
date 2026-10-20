import { IconPin, IconTrain } from "../ui/icons";
import { Chip } from "../ui/Chip";
import { Price } from "../ui/Price";
import { toolLabel } from "../../lib/toolMeta";
import { CardShell, NoData, Notice } from "./CardShell";
import { asRecord, numOf, strOf, toItemPage } from "./data";
import type { ToolCardProps } from "./types";

/**
 * LastMileCard — 末端接驳卡（`plan_last_mile` → last_mile.py 的 Envelope.data）。
 *
 * 为什么需要这张卡（生产会话实测）：`plan_last_mile` 是内核真能力（两次高德 geocode
 * 正算 + v3 跨城公交），但此前既没进注册表也没进可见性表 → 时间线落 ToolCardFallback，
 * 用户看到的是「未适配的结构化视图」+ 裸 JSON。
 *
 * payload 形状（kernel/aggregation/last_mile.py L110-113）：
 *   {hub: GeoLocation, site: GeoLocation, options: ItemPage, live_refs: string[]}
 *   hub/site 为高德五级行政区划 + location（"lng,lat" 原样）；
 *   options 由 amap_direction_transit 归一（name=线路拼接、price=cost 元、
 *   schedule.duration_minutes/stops），查不到时为空 ItemPage + degraded 附言。
 *
 * 红线②：只呈现 payload 里的字段，不补行政区划、不猜票价；`degraded` 原文转述
 * （它同时承载「班次日期仅支持 3 天内」与「公交查不到」两类附言）。
 * 红线④：票价一律走 `Price`（null → "暂无报价"，绝不 ¥0）。
 */

/** GeoLocation → 行政区划行：formatted_address 优先，缺段时按 省+市+县+乡 拼接。 */
function placeLine(raw: unknown): string {
  const g = asRecord(raw);
  const formatted = strOf(g.formatted_address);
  if (formatted) return formatted;
  return [strOf(g.province), strOf(g.city), strOf(g.district), strOf(g.township)]
    .filter(Boolean)
    .join("");
}

/** 分钟 → "3 小时 44 分"；null 不显示（不按 0 兜底）。 */
function durationText(minutes: number | null): string {
  if (minutes === null) return "";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} 分钟`;
  return m === 0 ? `${h} 小时` : `${h} 小时 ${m} 分`;
}

/** 端点行：枢纽 / 田野点（名称固定标在中性色，地址与坐标原样透传）。 */
function Endpoint({ role, geo }: { role: string; geo: Record<string, unknown> }) {
  const coords = strOf(geo.location);
  const place = placeLine(geo);
  return (
    <div className="flex items-start gap-2 text-[12px] leading-relaxed">
      <IconPin className="w-3.5 h-3.5 text-cinnabar shrink-0 mt-0.5" />
      <span className="text-inkfaint shrink-0 w-8">{role}</span>
      <div className="min-w-0 flex-1">
        <div className="text-ink">{place || "地名未返回"}</div>
        {coords && <div className="num text-[10.5px] text-inkfaint">{coords}</div>}
      </div>
    </div>
  );
}

export function LastMileCard({ data, degraded, error }: ToolCardProps) {
  const rec = asRecord(data);
  const hub = asRecord(rec.hub);
  const site = asRecord(rec.site);
  const options = toItemPage(rec.options);
  const hubName = strOf(hub.district) || strOf(hub.city) || strOf(hub.province);
  const siteName = strOf(site.district) || strOf(site.city) || strOf(site.province);
  const route = hubName || siteName ? `${hubName || "枢纽"} → ${siteName || "田野点"}` : undefined;
  const empty = data === null || data === undefined || (!hubName && !siteName && options.items.length === 0);
  // 失败原因优先于通用文案：hint 是可操作下一步（换标准地名 / 配 Key）
  const failure = (error?.hint || error?.message || "").trim();

  return (
    <CardShell
      title={toolLabel("plan_last_mile")}
      subtitle={route}
      icon={<IconTrain className="w-4 h-4" />}
      // degraded 原文是成句说明（「高德班次日期仅支持 3 天内…」），放卡头 chip 会撑破一行；
      // 卡头只留短标「降级」，原句在卡内 Notice 里完整转述。
      statusChip={
        <>
          {degraded && <Chip tone="ochre">降级</Chip>}
          {options.items.length === 0 && <Chip tone="ochre">未取得方案</Chip>}
        </>
      }
    >
      {empty ? (
        <NoData
          hint={failure || "未返回枢纽/田野点解析结果；可确认地名后重试 plan_last_mile。"}
        />
      ) : (
        <div className="space-y-2.5">
          <div className="hairline rounded-md bg-cardsunk/60 px-2.5 py-2 space-y-1">
            <Endpoint role="枢纽" geo={hub} />
            <Endpoint role="田野点" geo={site} />
          </div>

          {options.items.length === 0 ? (
            <div className="space-y-2">
              <Notice>
                {degraded ||
                  "未查到公交接驳方案（高德仅覆盖境内，部分乡镇无公交数据）。"}
              </Notice>
              <p className="text-[11.5px] text-inkfaint">
                下一步：可改用城际班车/包车等公路方式衔接；境外田野点暂不支持末端接驳。
              </p>
            </div>
          ) : (
            <>
              <ul className="space-y-1.5">
                {options.items.map((item, i) => {
                  const schedule = asRecord(item.schedule);
                  const minutes = numOf(schedule.duration_minutes);
                  const transfers = numOf(schedule.stops);
                  const meta = [
                    durationText(minutes),
                    transfers === null ? "" : transfers > 0 ? `换乘 ${transfers} 次` : "直达",
                  ].filter(Boolean).join(" · ");
                  return (
                    <li key={`${item.name}-${i}`} className="flex items-start gap-2">
                      <span className="num text-[10.5px] text-inkfaint shrink-0 w-4 mt-0.5">{i + 1}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-[12.5px] text-ink truncate" title={item.name}>
                          {item.name || "公交方案"}
                        </div>
                        {meta && <div className="text-[10.5px] text-inkfaint">{meta}</div>}
                      </div>
                      <span className="shrink-0">
                        <Price value={item.price} raw={item.price_raw ?? undefined} />
                      </span>
                    </li>
                  );
                })}
              </ul>
              {degraded && <Notice>{degraded}</Notice>}
            </>
          )}
        </div>
      )}
    </CardShell>
  );
}
