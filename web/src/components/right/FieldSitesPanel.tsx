import { useState, type ReactNode } from "react";
import { Chip } from "../ui/Chip";
import { EmptyState } from "../ui/EmptyState";
import { Price } from "../ui/Price";
import { IconBed, IconCheck, IconPin, IconPlane, IconTemple } from "../ui/icons";
import { ItemList } from "../cards/SearchResultCard";
import { asArray, asRecord, boolOf, numOf, strOf, toItemPage } from "../cards/data";
import { useProjectStore } from "../../stores/project";
import { useToolsStore } from "../../stores/tools";
import { rest } from "../../api/rest";

/**
 * FieldSitesPanel — 右栏「田野点」面板（状态投影）。
 *
 * 设计板同源：右栏 SiteProfilePanel lines 552-616
 *   站点头 lines 554-560：`flex items-center justify-between mb-3` + 名 `font-serif text-[16px] font-600
 *     flex items-center gap-1.5`（line 556，pin 朱砂）+ 副行 `text-[11px] text-inkfaint`（line 557）
 *     + 右侧 `chip bg-bamboowash text-bamboo border-bamboo/25`「已确认」（line 559）→ 复用 Task 5 `Chip`
 *   交通块 lines 562-577：`hairline rounded-lg bg-card p-3 mb-3` + 块头 `text-[11.5px] text-inksoft mb-2
 *     flex items-center gap-1.5`（line 564 靛蓝 plane）+ 条目行 lines 566-570 → 复用 Task 6 `ItemList`
 *   住宿块 lines 579-596：块头右侧元信息（line 581 `≤¥300 · 3 家`）+ `¥150/晚`（line 585 `/晚`
 *     `text-[9.5px] text-inkfaint`）→ `ItemList thumb unitSuffix="/晚"`
 *   文化资源图谱 lines 598-608：`chip bg-indigowash text-indigo border-indigo/15`（line 602）→ `Chip tone="indigo"`
 *   summary_hints 三格 lines 611-615：`rounded-lg bg-cardsunk hairline p-3 grid grid-cols-3 text-center`，
 *     中格 `border-x border-hairline`，标签 `text-[10px] text-inkfaint mt-0.5`
 *
 * 同源红线②：面板数据 = `snapshot.field_sites`（REST 初始快照）**叠加**本回合
 * `profile_field_site` 的 `tool_completed.data`（tools store），即中栏 SiteProfileCard 渲染的
 * 同一份 payload；面板 0 处 fetch、0 处数字加工（极值取内核 `summary.*_min`，缺失才回落 items 实价）。
 * 宿主在 loop.py L158-160 把 `{"name": data.location, "confirmed": False, "profile": data}` 落盘，
 * 故 record.name === profile.location，按名精确对齐（不做模糊匹配、不猜）；本回合新出画像在快照
 * 刷新前以「本回合新画像」条目呈现（宿主落盘 confirmed=False，故记为待确认）。
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

/** 画像分节块（设计板 lines 563-564 / 580-581 / 599-600 的块头 + 卡中卡）。 */
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
    <div className="hairline rounded-lg bg-card p-3 mb-3">
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

function SiteBlock({ site, onConfirm, confirming }: {
  site: SiteView;
  onConfirm?: (site: SiteView) => void;
  confirming?: string | null;
}) {
  const rec = site.profile ?? {};
  const summary = asRecord(rec.summary);
  const counts = asRecord(summary.section_counts);
  const transport = toItemPage(rec.transport);
  const accommodation = toItemPage(rec.accommodation);
  const culture = asArray(rec.culture);
  // summary 的最低价缺失时才回落分段实价极值；显式 null 保留（内核明确「无报价」）
  const transportMin =
    summary.transport_min === undefined ? transport.cheapest : numOf(summary.transport_min);
  const nightlyMin =
    summary.accommodation_nightly_min === undefined
      ? accommodation.cheapest
      : numOf(summary.accommodation_nightly_min);
  const cultureCount = numOf(counts.culture) ?? culture.length;
  const generatedAt = strOf(rec.generated_at);

  // 文化点位按内核标注的 category 分组（同 SiteProfileCard，不重新分类、不猜语义）
  const groups = new Map<string, string[]>();
  for (const raw of culture) {
    const c = asRecord(raw);
    const name = strOf(c.name);
    if (!name) continue;
    const cat = strOf(c.category);
    groups.set(cat, [...(groups.get(cat) ?? []), name]);
  }

  return (
    <section className="mb-5 last:mb-0">
      {/* 站点头（设计板 lines 554-560） */}
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="min-w-0">
          <div className="font-serif text-[16px] font-semibold flex items-center gap-1.5 text-ink">
            <IconPin className="w-4 h-4 text-cinnabar shrink-0" />
            <span className="truncate">{site.name || "未命名田野点"}</span>
          </div>
          {generatedAt && (
            <div className="num text-[11px] text-inkfaint">{`画像生成于 ${generatedAt}`}</div>
          )}
        </div>
        <span className="shrink-0 flex items-center gap-1.5">
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
          {/* 交通（设计板 lines 562-577） */}
          <Block
            icon={<IconPlane className="w-4 h-4 text-indigo" />}
            title="交通可达"
            meta={`${transport.items.length} 条`}
          >
            <ItemList
              items={transport.items}
              icon={<IconPlane className="w-4 h-4" />}
              limit={3}
              emptyText="未取得交通报价（未给出发城市或数据源无结果）"
            />
          </Block>

          {/* 住宿（设计板 lines 579-596） */}
          <Block
            icon={<IconBed className="w-4 h-4 text-cinnabar" />}
            title="长住住宿"
            meta={`${accommodation.items.length} 家`}
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

          {/* 文化资源图谱（设计板 lines 598-608） */}
          <Block
            icon={<IconTemple className="w-4 h-4 text-indigo" />}
            title="文化资源图谱"
            meta={`${culture.length} 处`}
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

  if (sites.length === 0) {
    return (
      <EmptyState
        icon={<IconPin className="w-4 h-4" />}
        title="尚无田野点"
        hint="在中栏说明目标地点与出发城市，罗盘会做四维画像并在此投影"
      />
    );
  }

  return (
    <div>
      <div className="font-serif text-[15px] font-semibold mb-3">{`田野点 · ${sites.length}`}</div>
      {refreshError && (
        <div className="flex items-center justify-between gap-2 text-[11px] text-ochre mb-2">
          <span>{refreshError}</span>
          <button type="button" onClick={retryRefresh} disabled={retrying}
                  className="underline disabled:opacity-50">重试</button>
        </div>
      )}
      {confirmError && <div className="text-[11px] text-ochre mb-2">{confirmError}</div>}
      {sites.map((site, i) => (
        <SiteBlock key={`${site.name}-${i}`} site={site}
                   onConfirm={confirmSite} confirming={confirming} />
      ))}
    </div>
  );
}
