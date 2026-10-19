import type { ReactNode } from "react";
import { Chip } from "../ui/Chip";
import { Price } from "../ui/Price";
import { IconBed, IconPin, IconPlane, IconSpark, IconTemple } from "../ui/icons";
import { CardShell, NoData, Notice } from "./CardShell";
import { AmapMapPanel, type MapMarker } from "./AmapMapPanel";
import { ItemList } from "./SearchResultCard";
import { asArray, asRecord, numOf, strOf, toItemPage } from "./data";
import type { ToolCardProps } from "./types";
import { LodgingComparison } from "./LodgingComparison";

/**
 * SiteProfileCard — 田野点画像卡（`profile_field_site` → FieldSiteProfile）。
 *
 * 设计板同源：右栏 SiteProfilePanel lines 552-616
 *   - 交通可达块 lines 563-577（`hairline rounded-lg bg-card p-3` + 块头 `text-[11.5px] text-inksoft`
 *     + 靛蓝图标 line 564）
 *   - 长住住宿块 lines 580-596（块头右侧 `≤¥300 · 3 家` 元信息 line 581）
 *   - 文化资源图谱 lines 599-608（indigo wash chip 网格）
 *   - summary_hints 三格 lines 611-615（`rounded-lg bg-cardsunk hairline p-3 grid grid-cols-3
 *     text-center`，中格 `border-x border-hairline`，数值 num 15px）
 *
 * 红线③④：住宿/交通最低价一律走 `Price`（source=cheapest，null → "暂无报价"，绝不 ¥0）；
 * 住宿零供给时如实转述内核 `summary.hints.accommodation` 的二段式建议（kernel/aggregation/
 * site_profile.py L183-185），不编造房源。
 */

/** section_counts / hints 的键 → 中文分节名（kernel 用英文 tag 落盘）。 */
const SECTION_LABEL: Record<string, string> = {
  accommodation: "住宿",
  transport: "交通",
  amenities: "配套",
  culture: "文化",
};

function label(key: string): string {
  return SECTION_LABEL[key] ?? key;
}

/** 画像分节块（设计板 lines 563-564/580-581 的块头 + 卡中卡）。 */
function Block({
  icon,
  title,
  meta,
  children,
}: {
  icon: ReactNode;
  title: string;
  meta?: string;
  children: ReactNode;
}) {
  return (
    <div className="hairline rounded-lg bg-card p-3">
      <div className="text-[11.5px] text-inksoft mb-2 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5">
          {icon}
          {title}
        </span>
        {meta && <span className="text-[10.5px] text-inkfaint">{meta}</span>}
      </div>
      {children}
    </div>
  );
}

export function SiteProfileCard({ data, degraded }: ToolCardProps) {
  const rec = asRecord(data);
  const summary = asRecord(rec.summary);
  const counts = asRecord(summary.section_counts);
  const hints = asRecord(summary.hints);
  const location = strOf(rec.location);
  const transport = toItemPage(rec.transport);
  const accommodation = toItemPage(rec.accommodation);
  const amenities = toItemPage(rec.amenities);
  const culture = asArray(rec.culture);
  // N6 C4 双源标注（PoC2 §4.4）：geo 有值 → 高德底座；缺段 → 文本回退（不猜坐标）
  const geo = asRecord(rec.geo);
  const geoLoc = strOf(geo.location);
  const facilities = toItemPage(rec.facilities);
  // 周边设施点位：内核附着的 facility_kind + POI 坐标（extra.location）原样透传
  const facMarkers = facilities.items
    .map((raw): MapMarker | null => {
      const f = asRecord(raw);
      const extra = asRecord(f.extra);
      const loc = strOf(extra.location);
      const name = strOf(f.name);
      const kind = strOf(extra.facility_kind);
      return loc && name ? { location: loc, title: name, kind: kind || undefined } : null;
    })
    .filter((mk): mk is MapMarker => mk !== null);
  // summary 的最低价缺失时才回退到分段实价极值；显式 null 保留（内核明确"无报价"）
  const transportMin =
    summary.transport_min === undefined ? transport.cheapest : numOf(summary.transport_min);
  const nightlyMin =
    summary.accommodation_nightly_min === undefined
      ? accommodation.cheapest
      : numOf(summary.accommodation_nightly_min);
  const cultureCount = numOf(counts.culture) ?? culture.length;
  const generatedAt = strOf(rec.generated_at);
  const hintEntries = Object.entries(hints).filter(([, v]) => strOf(v));

  // 文化点位按内核标注的 category（检索兴趣）分组呈现
  const groups = new Map<string, string[]>();
  for (const raw of culture) {
    const c = asRecord(raw);
    const name = strOf(c.name);
    if (!name) continue;
    const cat = strOf(c.category);
    groups.set(cat, [...(groups.get(cat) ?? []), name]);
  }

  const empty =
    data === null ||
    data === undefined ||
    (!location &&
      culture.length === 0 &&
      transport.items.length === 0 &&
      accommodation.items.length === 0 &&
      amenities.items.length === 0 &&
      Object.keys(counts).length === 0);

  return (
    <CardShell
      title="田野点画像"
      subtitle={location || undefined}
      icon={<IconPin className="w-4 h-4" />}
      degraded={degraded}
      statusChip={
        empty ? undefined : (
          <>
            {geoLoc ? (
              <Chip tone="indigo">高德底座</Chip>
            ) : (
              <Chip tone="ochre">文本回退</Chip>
            )}
            <Chip tone="bamboo">已生成</Chip>
          </>
        )
      }
    >
      {empty ? (
        <NoData hint="未取得田野点画像数据；可重试 profile_field_site 或先补充地点与出发城市。" />
      ) : (
        <div className="space-y-3">
          {/* summary_hints 三格（设计板 lines 611-615） */}
          <div className="rounded-lg bg-cardsunk hairline p-3 grid grid-cols-3 text-center">
            <div>
              <Price value={transportMin} source="cheapest" />
              <div className="text-[10px] text-inkfaint mt-0.5">交通最低</div>
            </div>
            <div className="border-x border-hairline">
              <Price value={nightlyMin} source="cheapest" />
              <div className="text-[10px] text-inkfaint mt-0.5">住宿/晚起</div>
            </div>
            <div>
              <div className="num text-[15px] text-ink font-semibold">{cultureCount}</div>
              <div className="text-[10px] text-inkfaint mt-0.5">文化点位</div>
            </div>
          </div>

          {/* 内核二段式建议（如住宿零供给）：原文转述，不改写数字 */}
          {hintEntries.map(([key, value]) => (
            <Notice key={key} tone="ochre">
              {strOf(value)}
            </Notice>
          ))}

          {/* N6 周边设施示意地图（PoC2 §5.2 嵌入点一）：geo 定位 + 设施点位；
              无 JS Key / loader 失败由 AmapMapPanel 降级链兑底（坐标清单仍可读） */}
          {(geoLoc || facMarkers.length > 0) && (
            <Block
              icon={<IconPin className="w-4 h-4 text-cinnabar" />}
              title="周边设施示意"
              meta={facMarkers.length > 0 ? `${facMarkers.length} 点位` : undefined}
            >
              <AmapMapPanel
                center={geoLoc || facMarkers[0]?.location}
                markers={facMarkers}
                height={200}
              />
            </Block>
          )}

          <Block
            icon={<IconPlane className="w-4 h-4 text-indigo" />}
            title="交通可达"
            meta={`${transport.items.length} 条`}
          >
            <ItemList items={transport.items} limit={3} emptyText="未取得交通报价（未给出发城市或数据源无结果）" />
          </Block>

          <Block
            icon={<IconBed className="w-4 h-4 text-cinnabar" />}
            title="长住住宿"
            meta={`${accommodation.items.length} 条`}
          >
            <ItemList
              items={accommodation.items}
              icon={<IconBed className="w-4 h-4" />}
              thumb
              unitSuffix="/晚"
              limit={3}
              emptyText="未取得住宿报价"
            />
          </Block>

          <Block
            icon={<IconTemple className="w-4 h-4 text-indigo" />}
            title="文化资源图谱"
            meta={`${culture.length} 条`}
          >
            {groups.size === 0 ? (
              <div className="text-[11.5px] text-inkfaint">未取得文化点位</div>
            ) : (
              <div className="space-y-2">
                {[...groups.entries()].map(([cat, names]) => (
                  <div key={cat || "未分类"}>
                    {cat && <div className="text-[10.5px] text-inkfaint mb-1">{cat}</div>}
                    <div className="flex flex-wrap gap-1.5">
                      {names.map((name, i) => (
                        <Chip key={`${name}-${i}`} tone="indigo">
                          {name}
                        </Chip>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Block>

          <Block
            icon={<IconSpark className="w-4 h-4 text-inksoft" />}
            title="当地配套与活动"
            meta={`${amenities.items.length} 条`}
          >
            <ItemList items={amenities.items} limit={3} emptyText="未取得配套条目" />
          </Block>

          <LodgingComparison data={rec.lodging_comparison} />
          {/* 分节计数 + 生成时间（可复核元信息） */}
          <div className="flex flex-wrap items-center gap-1.5">
            {Object.entries(counts).map(([key, value]) => (
              <Chip key={key} tone="neutral">
                {`${label(key)} ${numOf(value) ?? 0}`}
              </Chip>
            ))}
            {generatedAt && (
              <span className="num text-[10.5px] text-inkfaint ml-auto">{generatedAt}</span>
            )}
          </div>
        </div>
      )}
    </CardShell>
  );
}
