import type { CachedSearchSummary } from "../api/rest";
import type { ToolCall } from "../stores/tools";
import { getToolCard } from "../components/cards/registry";
import { visibilityOf } from "../lib/toolMeta";
import { ToolCardFallback } from "../components/cards/ToolCardFallback";

/**
 * 证据 tab 的合并与准入纯函数（spec C7）。
 * 三个正交概念（spec 评审 R13）：
 * - 证据身份：叶子=cid；跨层配对要求 call_id==parent_call_id 且 tool 相同；
 *   同(parent,tool)多缓存快照不互配（聚合内部双策略快照各占一行）。
 * - 同题分组：query_key 后端写入时生成（归一化参数 canonical JSON；文献检索键
 *   另含后端写入的研究范围指纹段，C04——切范围后同参检索不被旧结果吞掉），
 *   同 key 缓存行折叠；key 为空（旧数据）不参与去重。
 * - 时序：全用服务端时间轴。cached_at 为 µs 精度 isoformat，字典序即时间序；
 *   索引已保此序（Task 5）；实时行无服务端时间，层内保 tools store order，
 *   整体排到同回合缓存行之后（R15 规划期修正）。
 */
export interface EvidenceRow {
  /** 主键：缓存摘要用 id；未配对实时行用 call_id */
  key: string;
  tool: string;
  /** 查询描述：缓存行=query；实时行=由 args 推断的一行短语 */
  query: string;
  /** 服务端时间轴：缓存行=cached_at；实时行=undefined（层内 order 排序，R15） */
  cachedAt?: string;
  /** "cache" | "live" —— 是否已持久化（决定懒加载与重拉时机） */
  origin: "cache" | "live";
  /** 跨层配对：实时行的 call_id 与缓存行的 parent_call_id 相同且 tool 相同 → 合并（R13） */
  callId?: string;
  turnId?: string;
  seq?: number;
  empty?: boolean;
  truncated?: boolean;
  total?: number;
  /** 文献失败行标记（零命中不算失败，尾标落「空结果」；其余工具失败行仍不入列） */
  failed?: boolean;
}

/** 实时行 query 短语：args.query / keyword / name / city / keywords / dest_name
 * 依次取首个非空串（不占造）；dest_name 是飞猪搜索原语的目标地名（住宿/航班无 query 键）。 */
function liveQuery(args: Record<string, unknown>): string {
  for (const k of ["query", "keyword", "name", "city", "keywords", "dest_name"]) {
    const v = args[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

/** C7-2 实时判空（仅 done 行，R10；C05 后失败文献行也用于判空）：data 无 items 且
 *  total===0 且无 answer——
 *  answer 不算空（R1：AI 答案是有价值内容）；无 items 键的非 ItemPage 形状不误杀。 */
function liveIsEmpty(t: ToolCall): boolean {
  const d = t.data;
  if (typeof d !== "object" || d === null) return false;
  const rec = d as Record<string, unknown>;
  const answer = rec["answer"];
  if (typeof answer === "string" && answer.trim()) return false;
  const items = rec["items"];
  return Array.isArray(items) && items.length === 0 && Number(rec["total"] ?? 0) === 0;
}

/** 证据行的时间归属标签（评审 P1-1）。
 *
 * 原实现把所有实时行一律标「本轮」，而 tools store 只在换项目时 reset（App 的跨项目
 * reset effect），跨回合不清——于是一小时内第 3 回合看到的第 1 回合实时行仍写「本轮」，
 * 界面在说谎。
 *
 * 现在按 `EvidenceRow.turnId` 与 `conversation.currentTurnId` 比对：只有当前（或刚结束的
 * 那个）回合标「本轮」，更早的实时行标「本次会话」。turnId 缺失（旧后端事件不含
 * turn_id）时不臆猜归属，同样按「本次会话」——宁可少说，不可说错。
 *
 * 截断标记（「已截断」）优先级更高，由调用方在外部判定（它还依赖缓存详情的 truncated）。 */
export function evidenceTurnLabel(row: EvidenceRow, currentTurnId: string | null): string {
  if (row.origin === "cache") return "已归档";
  return row.turnId && currentTurnId && row.turnId === currentTurnId ? "本轮" : "本次会话";
}

/**
 * 合并两源为证据行列表。
 * cached 须来自 rest.listCachedSearches（索引序即时间序，Task 5）；
 * live 须来自 tools store 的 order 序（仅 settled 调用，调用方过滤）。
 */
export function mergeEvidence(
  cached: CachedSearchSummary[],
  live: ToolCall[],
): EvidenceRow[] {
  // 准入（C2/C7-1/C7-2）：只收 collapsed 级（Task 9 重分类后）且有专用卡
  // （Fallback=裸 JSON，剔除）、索引已判空的不入（empty===true 剔除；null=
  // 旧库 unknown 不预过滤，R12）；未注册工具默认不入（不臆造展示）。
  // C05 例外：文献检索的空结果仍入列——卡片空态自带补检入口，是用户唯一可复检处
  const cacheAdmitted = cached.filter(
    (c) => visibilityOf(c.tool) === "collapsed"
      // C05：文献空结果仍入列（卡片空态带补检入口）；其余工具维持剔除
      && (c.tool === "search_literature" || c.empty !== true)
      && getToolCard(c.tool) !== ToolCardFallback,
  );

  // 同题折叠（C02）：query_key 非空的只留最新一条（索引序=时间序，后到者胜——
  // 旧实现留首条，新结果刷新后仍被折叠消失）；空 key 旧数据全保留。
  // 两遍法：先记每个 key 的最后一条 id，再过滤。
  // 注意（评审 R06）：折叠结果只用于**输出**；跨层配对必须拿折叠前的全量缓存行判定，
  // 否则已折叠旧快照对应的实时行会因“找不到缓存”而被当作未配对实时结果重新出现。
  const latestByKey = new Map<string, string>();
  for (const c of cacheAdmitted) {
    if (c.query_key) latestByKey.set(c.query_key, c.id);
  }
  const cacheRows: EvidenceRow[] = [];
  for (const c of cacheAdmitted) {
    if (c.query_key && latestByKey.get(c.query_key) !== c.id) continue;
    cacheRows.push({
      key: c.id, tool: c.tool, query: c.query, cachedAt: c.cached_at,
      origin: "cache", callId: c.parent_call_id || undefined,
      turnId: c.turn_id || undefined, empty: c.empty ?? undefined,
      truncated: c.truncated ?? undefined, total: c.total ?? undefined,
    });
  }

  // 实时行准入（C2/C7-1/C7-2/C7-3）：仅 done（failed 剔除——失败/降级由对话
  // tab 的 ToolActivityStrip 承担，不在证据 tab 重复；C05 仅对文献检索破例，
  // 因其失败态是用户唯一可复检入口）；collapsed 级且有专用卡；
  // 空结果剔除（answer 不算空；同样对文献检索破例）；running 不入列
  const liveAdmitted = live.filter(
    (t) =>
      // C05：文献失败行仍入列（LiteratureCard 失败态/补检入口可达）；
      // 其余工具维持 done-only、空结果剔除（失败由对话 tab 活动条承担，C7-3）
      (t.status === "done" || (t.tool === "search_literature" && t.status === "failed")) &&
      visibilityOf(t.tool) === "collapsed" &&
      getToolCard(t.tool) !== ToolCardFallback &&
      (t.tool === "search_literature" || !liveIsEmpty(t)),
  );

  // 跨层配对：实时 call_id == 缓存 parent_call_id 且 tool 相同 → 实时行不单列（R13）。
  // 同(parent,tool)多缓存行不互配——配对只发生在 实时↔缓存 之间。
  // R06：配对集合取自**折叠前**的全量合格缓存行——同一会话内两次同题查询时，
  // 旧快照已被同题折叠移出默认列表，但它对应的实时行同样属于历史，不能以
  // “未配对”身份重新附加到最新结果之后（否则刷新前多一行、刷新后消失）。
  const cachedCalls = new Set(
    cacheAdmitted
      .filter((c) => c.parent_call_id)
      .map((c) => `${c.tool}\u0000${c.parent_call_id}`),
  );
  const pairedLiveKeys = new Set<string>();
  for (const t of liveAdmitted) {
    if (cachedCalls.has(`${t.tool}\u0000${t.call_id}`)) {
      pairedLiveKeys.add(t.call_id);
      continue;  // 缓存已覆盖该调用；未覆盖的失败调用在准入已剔除（C7-3），
                 // 不产生占位——R4「成功不被失败遮掉」由此更强地成立。
                 // C05 例外：文献失败行在准入保留，无缓存覆盖时会单列（补检入口）
    }
  }

  const liveRows: EvidenceRow[] = liveAdmitted
    .filter((t) => !pairedLiveKeys.has(t.call_id))
    .map((t) => ({
      key: t.call_id, tool: t.tool, query: liveQuery(t.args),
      origin: "live", callId: t.call_id, turnId: t.turnId, seq: t.seq,
      empty: liveIsEmpty(t) || undefined,
      failed: (t.status === "failed" && !liveIsEmpty(t)) || undefined,
      truncated: undefined, total: undefined,
    }));

  // 时序：缓存行保索引序（时间序），实时行层内保 store order，整体在后（R15）
  return [...cacheRows, ...liveRows];
}
