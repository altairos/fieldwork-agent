import { Chip } from "../ui/Chip";
import { IconBed, IconPin, IconPlane, IconSpark, IconTemple } from "../ui/icons";
import { CardShell, NoData } from "./CardShell";
import { ItemList } from "./SearchResultCard";
import { asRecord, strOf } from "./data";
import { LodgingComparison } from "./LodgingComparison";
import {
  CultureGroups,
  FacilitiesPanel,
  geoLocation,
  ProfileBlock,
  ProfileHints,
  profileNumbers,
  SectionMeta,
  SummaryTriple,
} from "./SiteProfileParts";
import { SpatialResults } from "./SpatialResults";
import type { ToolCardProps } from "./types";

/**
 * SiteProfileCard — 田野点画像卡（`profile_field_site` → FieldSiteProfile）。
 *
 * 位置（2026-10-20 用户反馈）：画像**不再进中栏对话流**（lib/toolMeta.TIMELINE_OMIT），
 * 右栏「田野点」面板是它的唯一出口。本组件仍保留在注册表里（注册表键集与内核对齐，
 * 见 registry.tsx 契约），并复用与右栏完全相同的呈现块（SiteProfileParts），
 * 因此两处渲染口径不会漂移——将来若要恢复内联，删掉 TIMELINE_OMIT 里的键即可。
 *
 * 红线③④：住宿/交通最低价一律走 `Price`（source=cheapest，null → "暂无报价"，绝不 ¥0）；
 * 住宿零供给时如实转述内核 `summary.hints.accommodation` 的两段式建议。
 */
export function SiteProfileCard({ data, degraded }: ToolCardProps) {
  const rec = asRecord(data);
  const location = strOf(rec.location);
  const geoLoc = geoLocation(rec);
  const generatedAt = strOf(rec.generated_at);
  const nums = profileNumbers(rec);

  const empty =
    data === null ||
    data === undefined ||
    (!location &&
      nums.culture.length === 0 &&
      nums.transport.items.length === 0 &&
      nums.accommodation.items.length === 0 &&
      nums.amenities.items.length === 0 &&
      Object.keys(nums.counts).length === 0);

  return (
    <CardShell
      title="田野点画像"
      subtitle={location || undefined}
      icon={<IconPin className="w-4 h-4" />}
      degraded={degraded}
      statusChip={
        empty ? undefined : (
          <>
            {geoLoc ? <Chip tone="indigo">高德底座</Chip> : <Chip tone="ochre">文本回退</Chip>}
            <Chip tone="bamboo">已生成</Chip>
          </>
        )
      }
    >
      {empty ? (
        <NoData hint="未取得田野点画像数据；可重试 profile_field_site 或先补充地点与出发城市。" />
      ) : (
        <div className="space-y-2.5">
          <SummaryTriple
            transportMin={nums.transportMin}
            nightlyMin={nums.nightlyMin}
            cultureCount={nums.cultureCount}
          />
          <ProfileHints hints={asRecord(nums.summary.hints)} />
          <FacilitiesPanel profile={rec} height={200} />

          <ProfileBlock
            icon={<IconPlane className="w-4 h-4 text-indigo" />}
            title="交通可达"
            meta={`${nums.transport.items.length} 条`}
          >
            <ItemList
              items={nums.transport.items}
              limit={3}
              emptyText="未取得交通报价（未给出发城市或数据源无结果）"
            />
          </ProfileBlock>

          <ProfileBlock
            icon={<IconBed className="w-4 h-4 text-cinnabar" />}
            title="长住住宿"
            meta={`${nums.accommodation.items.length} 条`}
          >
            <ItemList
              items={nums.accommodation.items}
              icon={<IconBed className="w-4 h-4" />}
              thumb
              unitSuffix="/晚"
              limit={3}
              emptyText="未取得住宿报价"
            />
          </ProfileBlock>

          <ProfileBlock
            icon={<IconTemple className="w-4 h-4 text-indigo" />}
            title="文化资源图谱"
            meta={`${nums.culture.length} 条`}
          >
            <CultureGroups culture={nums.culture} />
          </ProfileBlock>

          <ProfileBlock
            icon={<IconSpark className="w-4 h-4 text-inksoft" />}
            title="当地配套与活动"
            meta={`${nums.amenities.items.length} 条`}
          >
            <ItemList items={nums.amenities.items} limit={3} emptyText="未取得配套条目" />
          </ProfileBlock>

          <LodgingComparison data={rec.lodging_comparison} />
          <SpatialResults data={rec.spatial_results} />
          <SectionMeta counts={nums.counts} generatedAt={generatedAt} />
        </div>
      )}
    </CardShell>
  );
}
