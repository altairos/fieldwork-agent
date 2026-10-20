import { useState } from "react";
import { Chip } from "../ui/Chip";
import { EmptyState } from "../ui/EmptyState";
import { IconBed, IconCheck, IconPin, IconPlane, IconSpark, IconTemple } from "../ui/icons";
import { ItemList } from "../cards/SearchResultCard";
import { asRecord, boolOf, strOf } from "../cards/data";
import { LodgingComparison } from "../cards/LodgingComparison";
import {
  CultureGroups,
  FacilitiesPanel,
  geoLocation,
  ProfileBlock,
  ProfileHints,
  profileNumbers,
  SectionMeta,
  SummaryTriple,
} from "../cards/SiteProfileParts";
import { SpatialResults } from "../cards/SpatialResults";
import { formatDate } from "../../lib/datetime";
import { useProjectStore } from "../../stores/project";
import { useToolsStore } from "../../stores/tools";
import { rest } from "../../api/rest";

/**
 * FieldSitesPanel — 右栏「田野点」面板（实时状态）。
 *
 * 定位（2026-10-20 用户反馈）：中栏不再内联田野点画像卡（lib/toolMeta.TIMELINE_OMIT），
 * 本面板成为画像的**唯一出口**，因此把原先只在中栏卡里出现的六块补齐：
 * 周边设施地图、内核二段式建议、当地配套与活动、住宿与通勤比较、候选观察场所、
 * 分节计数 + 高德底座/文本回退来源徽标。呈现块与 SiteProfileCard 共用
 * `cards/SiteProfileParts`，两处不会各说各话。
 *
 * 设计板同源：右栏 SiteProfilePanel lines 552-616（站点头 / 交通块 / 住宿块 /
 * 文化 chip 网格 / summary_hints 三格）。
 *
 * 同源红线②：面板数据 = `snapshot.field_sites`（REST 初始快照）**叠加**本回合
 * `profile_field_site` 的 `tool_completed.data`（tools store），面板 0 处 fetch、
 * 0 处数字加工（极值取内核 `summary.*_min`，缺失才回落 items 实价）。
 * 宿主在 loop.py L158-160 把 `{"name": data.location, "confirmed": False, "profile": data}`
 * 落盘，故 record.name === profile.location，按名精确对齐（不做模糊匹配、不猜）。
 * 红线④：价格一律走 `Price`（null → "暂无报价"，绝不 ¥0）。
 */

/** 面板内的站点视图：快照记录 + 本回合 live 画像（同源 data）合并结果。 */
interface SiteView {
  /** repo 记录键（site_ 前缀）；live-only 项为 ""（快照尚未落盘，不可确认） */
  id: string;
  name: string;
  confirmed: boolean;
  /** FieldSiteProfile 的 dict 形式；null = 尚未取得画像 */
  profile: Record<string, unknown> | null;
  /** true = 仅存在于本回合 tool_completed.data（快照尚未刷新） */
  liveOnly: boolean;
  degraded: string | null;
}

/** 非空对象 → Record；null/空对象 → null（区分「无画像」与「有画像」）。 */
function profileOf(raw: unknown): Record<string, unknown> | null {
  if (raw === null || raw === undefined) return null;
  const rec = asRecord(raw);
  return Object.keys(rec).length > 0 ? rec : null;
}

function SiteBlock({ site, onConfirm, confirming }: {
  site: SiteView;
  onConfirm?: (site: SiteView) => void;
  confirming?: string | null;
}) {
  const rec = site.profile ?? {};
  const nums = profileNumbers(rec);
  const geoLoc = geoLocation(rec);
  const generatedAt = strOf(rec.generated_at);

  return (
    <section className="mb-4 last:mb-0">
      {/* 站点头（设计板 lines 554-560） */}
      <div className="flex items-center justify-between gap-2 mb-2.5">
        <div className="min-w-0">
          <div className="font-serif text-[15px] font-semibold flex items-center gap-1.5 text-ink">
            <IconPin className="w-4 h-4 text-cinnabar shrink-0" />
            <span className="truncate">{site.name || "未命名田野点"}</span>
          </div>
          {generatedAt && (
            <div className="num text-[10.5px] text-inkfaint">{`画像生成于 ${formatDate(generatedAt)}`}</div>
          )}
        </div>
        <span className="shrink-0 flex flex-wrap items-center justify-end gap-1.5">
          {site.profile !== null && (geoLoc
            ? <Chip tone="indigo">高德底座</Chip>
            : <Chip tone="ochre">文本回退</Chip>)}
          {site.liveOnly && <Chip tone="indigo">本回合新画像</Chip>}
          {site.degraded && <Chip tone="ochre">降级 · {site.degraded}</Chip>}
          {site.confirmed ? (
            <Chip tone="bamboo">
              <IconCheck className="w-3 h-3" />
              已确认
            </Chip>
          ) : site.liveOnly || !site.id ? (
            <Chip tone="neutral">待确认</Chip>
          ) : (
            <button
              type="button"
              disabled={confirming !== null}
              onClick={() => onConfirm?.(site)}
              className="text-[11.5px] px-2.5 py-0.5 rounded-md hairline text-cinnabar hover:bg-cardsunk disabled:opacity-50"
            >
              {confirming === site.id ? "确认中…" : "确认"}
            </button>
          )}
        </span>
      </div>

      {site.profile === null ? (
        <div className="hairline rounded-lg bg-card p-3 text-[11.5px] text-inkfaint">
          尚未取得该点画像（profile_field_site 未返回）；可在中栏请罗盘补做画像。
        </div>
      ) : (
        <>
          <SummaryTriple
            transportMin={nums.transportMin}
            nightlyMin={nums.nightlyMin}
            cultureCount={nums.cultureCount}
          />

          <ProfileHints hints={asRecord(nums.summary.hints)} className="mt-2.5 mb-2.5" />

          <FacilitiesPanel profile={rec} className="mt-2.5" />

          <ProfileBlock
            icon={<IconPlane className="w-4 h-4 text-indigo" />}
            title="交通可达"
            meta={`${nums.transport.items.length} 条`}
          >
            <ItemList
              items={nums.transport.items}
              icon={<IconPlane className="w-4 h-4" />}
              limit={3}
              emptyText="未取得交通报价（未给出发城市或数据源无结果）"
            />
          </ProfileBlock>

          <ProfileBlock
            icon={<IconBed className="w-4 h-4 text-cinnabar" />}
            title="长住住宿"
            meta={`${nums.accommodation.items.length} 家`}
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
            icon={<IconSpark className="w-4 h-4 text-inksoft" />}
            title="当地配套与活动"
            meta={`${nums.amenities.items.length} 条`}
          >
            <ItemList items={nums.amenities.items} limit={3} emptyText="未取得配套条目" />
          </ProfileBlock>

          <ProfileBlock
            icon={<IconTemple className="w-4 h-4 text-indigo" />}
            title="文化资源图谱"
            meta={`${nums.culture.length} 处`}
          >
            <CultureGroups culture={nums.culture} />
          </ProfileBlock>

          {Array.isArray(rec.lodging_comparison) && rec.lodging_comparison.length > 0 && (
            <div className="hairline rounded-lg bg-card p-2.5 mb-2.5">
              <LodgingComparison data={rec.lodging_comparison} />
            </div>
          )}
          {Array.isArray(rec.spatial_results) && rec.spatial_results.length > 0 && (
            <div className="hairline rounded-lg bg-card p-2.5 mb-2.5">
              <SpatialResults data={rec.spatial_results} />
            </div>
          )}

          <SectionMeta counts={nums.counts} generatedAt={generatedAt} />
        </>
      )}
    </section>
  );
}

export function FieldSitesPanel() {
  const snapshot = useProjectStore((s) => s.snapshot);
  const refreshError = useProjectStore((s) => s.refreshError);   // R06b：回合末回读失败提示
  const byCallId = useToolsStore((s) => s.byCallId);
  const order = useToolsStore((s) => s.order);

  // 本回合 live 画像：location（缺失时回落 args.location）→ data，后到覆盖先到（最新一次为准）
  const live = new Map<string, { data: Record<string, unknown>; degraded: string | null }>();
  for (const id of order) {
    const call = byCallId[id];
    if (!call || call.tool !== "profile_field_site" || call.status !== "done") continue;
    const data = asRecord(call.data);
    const key = strOf(data.location) || strOf(call.args.location);
    if (!key) continue;
    live.set(key, { data, degraded: call.degraded ?? null });
  }

  const [confirming, setConfirming] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  // R06b：重试走与 R06a 相同的回读路径——成功清提示，失败保持提示（如实反馈）。
  const retryRefresh = async () => {
    const pid = snapshot?.project_id;
    if (!pid || retrying) return;
    setRetrying(true);
    try {
      useProjectStore.getState().loadSnapshot(await rest.getProject(pid));
      useProjectStore.getState().setRefreshError(null);
    } catch {
      useProjectStore.getState().setRefreshError("快照刷新失败，最新田野点/行程可能未显示");
    } finally {
      setRetrying(false);
    }
  };

  const confirmSite = async (site: SiteView) => {
    const pid = snapshot?.project_id;
    if (!pid || !site.id || confirming !== null) return;
    setConfirming(site.id);
    setConfirmError(null);
    try {
      await rest.confirmFieldSite(pid, site.id);
      useProjectStore.getState().loadSnapshot(await rest.getProject(pid));
    } catch (e) {
      setConfirmError(e instanceof Error ? e.message : String(e));
    } finally {
      setConfirming(null);
    }
  };

  const records = snapshot?.field_sites ?? [];
  const sites: SiteView[] = records.map((r) => {
    const name = strOf(r.name);
    const hit = live.get(name);
    return {
      id: strOf(r.id),
      name,
      confirmed: boolOf(r.confirmed),
      profile: hit ? hit.data : profileOf(r.profile),
      liveOnly: false,
      degraded: hit?.degraded ?? null,
    };
  });
  for (const [name, hit] of live) {
    if (records.some((r) => strOf(r.name) === name)) continue;
    sites.push({ id: "", name, confirmed: false, profile: hit.data, liveOnly: true, degraded: hit.degraded });
  }

  // R06b 提示条（含重试）：空站点与列表两个返回分支共用——
  // 空态不能吞掉提示（终审 Minor #1：项目刚建尚无田野点、回合后回读失败时仍需可见可重试）。
  const refreshBanner = refreshError && (
    <div className="flex items-center justify-between gap-2 text-[11px] text-ochre mb-2">
      <span>{refreshError}</span>
      <button type="button" onClick={retryRefresh} disabled={retrying}
              className="underline disabled:opacity-50">重试</button>
    </div>
  );

  if (sites.length === 0) {
    return (
      <div>
        {refreshBanner}
        <EmptyState
          icon={<IconPin className="w-4 h-4" />}
          title="尚无田野点"
          hint="在中栏说明目标地点与出发城市，罗盘会做四维画像并在此投影"
        />
      </div>
    );
  }

  return (
    <div>
      <div className="font-serif text-[15px] font-semibold mb-2.5">{`田野点 · ${sites.length}`}</div>
      {refreshBanner}
      {confirmError && <div className="text-[11px] text-ochre mb-2">{confirmError}</div>}
      {sites.map((site, i) => (
        <SiteBlock key={`${site.name}-${i}`} site={site}
                   onConfirm={confirmSite} confirming={confirming} />
      ))}
    </div>
  );
}
