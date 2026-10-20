import type { ReactNode } from "react";
import { Chip } from "../ui/Chip";
import { Price } from "../ui/Price";
import { IconPin } from "../ui/icons";
import { formatDate } from "../../lib/datetime";
import { AmapMapPanel, type MapMarker } from "./AmapMapPanel";
import { Notice } from "./CardShell";
import { asArray, asRecord, numOf, strOf, toItemPage } from "./data";

/**
 * SiteProfileParts — 田野点画像的**共用呈现块**。
 *
 * 为什么抽出来（用户反馈）：画像原先在中栏（SiteProfileCard）与右栏（FieldSitesPanel）
 * 各写一套，于是中栏有地图/内核建议/住宿比较/空间检索结果、右栏没有；右栏有确认状态、
 * 中栏没有。现在中栏不再内联画像（lib/toolMeta.TIMELINE_OMIT），右栏是唯一出口，
 * 两边必须同源——同源的办法不是"抄一遍"，而是共用这里的块。
 *
 * 口径（红线②③④）：
 * - 只读 payload 字段，不补文本、不猜语义；
 * - 最低价「内核 summary 显式给出就用它，字段缺失才回落分段实价极值，显式 null 保留」；
 * - 价格一律走 `Price`（null → "暂无报价"，绝不 ¥0）。
 */

/** 分节键 → 中文名（内核用英文 tag 落盘）。 */
const SECTION_LABEL: Record<string, string> = {
  accommodation: "住宿",
  transport: "交通",
  amenities: "配套",
  culture: "文化",
};

/** 画像数值口径（两处面板唯一来源；改这里两边同时生效）。 */
export function profileNumbers(profile: Record<string, unknown>) {
  const summary = asRecord(profile.summary);
  const counts = asRecord(summary.section_counts);
  const transport = toItemPage(profile.transport);
  const accommodation = toItemPage(profile.accommodation);
  const amenities = toItemPage(profile.amenities);
  const facilities = toItemPage(profile.facilities);
  const culture = asArray(profile.culture);
  const transportMin =
    summary.transport_min === undefined ? transport.cheapest : numOf(summary.transport_min);
  const nightlyMin =
    summary.accommodation_nightly_min === undefined
      ? accommodation.cheapest
      : numOf(summary.accommodation_nightly_min);
  const cultureCount = numOf(counts.culture) ?? culture.length;
  return {
    summary, counts, transport, accommodation, amenities, facilities, culture,
    transportMin, nightlyMin, cultureCount,
  };
}

/** 分节块（块头 + 卡中卡）。 */
export function ProfileBlock({
  icon,
  title,
  meta,
  className = "",
  children,
}: {
  icon: ReactNode;
  title: string;
  meta?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`hairline rounded-lg bg-card p-2.5 mb-2.5 ${className}`.trim()}>
      <div className="text-[11.5px] text-inksoft mb-2 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5">
          {icon}
          {title}
        </span>
        {meta && <span className="text-[10.5px] text-inkfaint num">{meta}</span>}
      </div>
      {children}
    </div>
  );
}

/** 三格指标：交通最低 / 住宿每晚起 / 文化点位。 */
export function SummaryTriple({
  transportMin,
  nightlyMin,
  cultureCount,
}: {
  transportMin: number | null;
  nightlyMin: number | null;
  cultureCount: number;
}) {
  return (
    <div className="rounded-lg bg-cardsunk hairline p-2.5 grid grid-cols-3 text-center">
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
  );
}

/** 文化点位按内核标注的 category 分组（不重新分类、不猜语义）。 */
export function CultureGroups({ culture }: { culture: unknown[] }) {
  const groups = new Map<string, string[]>();
  for (const raw of culture) {
    const c = asRecord(raw);
    const name = strOf(c.name);
    if (!name) continue;
    const cat = strOf(c.category);
    groups.set(cat, [...(groups.get(cat) ?? []), name]);
  }
  if (groups.size === 0) {
    return <div className="text-[11.5px] text-inkfaint">未取得文化点位</div>;
  }
  return (
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
  );
}

/** 内核二段式建议（如住宿零供给 → 枢纽城市基地 + 当地学术接待）：原文转述。 */
export function ProfileHints({
  hints,
  className = "",
}: {
  hints: Record<string, unknown>;
  className?: string;
}) {
  const entries = Object.entries(hints).filter(([, v]) => strOf(v));
  if (entries.length === 0) return null;
  return (
    <>
      {entries.map(([key, value]) => (
        <Notice key={key} tone="ochre" className={className}>
          {strOf(value)}
        </Notice>
      ))}
    </>
  );
}

/** 周边设施点位（facility_kind + POI 坐标）→ 地图标记。 */
export function facilityMarkers(profile: Record<string, unknown>): MapMarker[] {
  return toItemPage(profile.facilities).items
    .map((raw): MapMarker | null => {
      const f = asRecord(raw);
      const extra = asRecord(f.extra);
      const location = strOf(extra.location);
      const name = strOf(f.name);
      const kind = strOf(extra.facility_kind);
      return location && name ? { location, title: name, kind: kind || undefined } : null;
    })
    .filter((mk): mk is MapMarker => mk !== null);
}

/** 高德底座坐标（缺段为空串 → 由调用方标「文本回退」）。 */
export function geoLocation(profile: Record<string, unknown>): string {
  return strOf(asRecord(profile.geo).location);
}

/**
 * 周边设施示意：地图 + 设施名清单（按 facility_kind 分组）。
 * 无坐标也无点位时不渲染（不留空壳）。
 */
export function FacilitiesPanel({
  profile,
  height = 180,
  className = "",
}: {
  profile: Record<string, unknown>;
  height?: number;
  className?: string;
}) {
  const geoLoc = geoLocation(profile);
  const markers = facilityMarkers(profile);
  if (!geoLoc && markers.length === 0) return null;
  const byKind = new Map<string, string[]>();
  for (const mk of markers) {
    const kind = mk.kind ?? "";
    byKind.set(kind, [...(byKind.get(kind) ?? []), mk.title]);
  }
  return (
    <ProfileBlock
      icon={<IconPin className="w-4 h-4 text-cinnabar" />}
      title="周边设施示意"
      meta={markers.length > 0 ? `${markers.length} 点位` : undefined}
      className={className}
    >
      <AmapMapPanel
        center={geoLoc || markers[0]?.location}
        markers={markers}
        height={height}
        showMarkerChips={false}
      />
      {byKind.size > 0 && (
        <div className="mt-2 space-y-1">
          {[...byKind.entries()].map(([kind, names]) => (
            <div key={kind || "其他"} className="flex flex-wrap gap-1.5 items-center">
              {kind && <span className="text-[10px] text-inkfaint">{kind}</span>}
              {names.slice(0, 6).map((name, i) => (
                <Chip key={`${name}-${i}`} tone="neutral">
                  {name}
                </Chip>
              ))}
              {names.length > 6 && (
                <span className="text-[10px] text-inkfaint">{`+${names.length - 6}`}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </ProfileBlock>
  );
}

/** 分节计数 + 生成时间（可复核元信息）。 */
export function SectionMeta({
  counts,
  generatedAt,
}: {
  counts: Record<string, unknown>;
  generatedAt?: string;
}) {
  const entries = Object.entries(counts);
  if (entries.length === 0 && !generatedAt) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {entries.map(([key, value]) => (
        <Chip key={key} tone="neutral">
          {`${SECTION_LABEL[key] ?? key} ${numOf(value) ?? 0}`}
        </Chip>
      ))}
      {generatedAt && (
        <span className="num text-[10.5px] text-inkfaint ml-auto">{formatDate(generatedAt)}</span>
      )}
    </div>
  );
}
