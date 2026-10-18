import { Chip } from "../ui/Chip";
import { PulseDot, type DotTone } from "../ui/PulseDot";
import { IconAlert, IconCheck } from "../ui/icons";
import { toolLabel } from "../cards/toolLabels";
import { useToolsStore, type ToolCall } from "../../stores/tools";

/**
 * ToolActivityStrip — AGENT 工具活动条（紧凑列表，一行一次调用）。
 *
 * 设计板同源：中栏工具活动条 lines 443-448
 *   容器 line 443 `hairline rounded-lg bg-card px-4 py-3 space-y-2`
 *   小标 line 444 `text-[10.5px] tracking-[.16em] text-inkfaint mb-1`「AGENT 工具活动」
 *   行 line 445 `flex items-center gap-2 text-[12.5px]` + `dot`（PulseDot）+
 *      工具名 `num text-inksoft`（line 445 `compose_itinerary`）+ 简述 `text-inkfaint`（「· 昆明→喜洲 · 90 天」）
 *   完成态 line 445 尾部 `w-3.5 h-3.5 text-bamboo ml-auto` check 图标
 *   降级态 line 447：整行 `opacity-60` + 工具名 `line-through decoration-inkfaint/60` +
 *      `chip bg-ochrewash text-ochre border-ochre/25 ml-1`「降级 · flyai_unavailable」+ 尾部 `text-inkfaint ml-auto text-[11px]`
 *   进行中：`dot bg-cinnabar animate-pulse`（设计板 line 264 的进行中态语义，PulseDot 已内建）
 *   配额用尽（error.code=flyai_rate_limited）：行尾标「每日共享配额已用尽」，
 *      条下渲染一次性通知卡（配额说明 + 申请自己的 API Key 外链）
 *
 * 过滤规则（C 节问题 2，对话流精简）：只留「活的和有问题的」——
 *   - update_research_context 不显示（内部状态写入，含失败重试噪音；正文自会解释）；
 *   - 成功且未降级（done && !degraded）不显示——成功活动的结果呈现由时间线 settled 区
 *     卡片/折叠摘要条承担，信息不丢失、密度下降；
 *   - running / failed / degraded 保留（进行中提示 + 异常提醒，语义从"全程审计视图"
 *     变为"进行中 + 异常"）。QuotaNoticeCard 逻辑不变。
 *
 * 收敛规则（C 节问题 3）：行标题一律用中文名（toolLabel，不露英文工具名）；
 *   同一工具**连续多次失败**按 order 聚合为一行（「连续 N 次失败」，不展开 args），
 *   消除重试风暴噪音；不同工具、非连续、running/degraded 仍逐条。
 *
 * 同源红线②：只读 tools store（tool_started/tool_completed 归约结果），不发请求、不转述数字；
 * 简述取内核下发的 `purpose` 原文，缺失时回落 `args` 的标量键值（原样呈现，不加工、不猜语义）。
 */

/** 每日共享配额用尽的错误码与申请页地址（与 kernel.sources.flyai._RATE_LIMIT_HINT 同源）。 */
const RATE_LIMIT_CODE = "flyai_rate_limited";
const FLYAI_KEY_URL = "https://flyai.open.fliggy.com/";

/** args → 简述：只取标量值（字符串/数字/布尔），最多 3 项，原样 `key value`。 */
function argsSummary(args: Record<string, unknown>, limit = 3): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(args)) {
    if (parts.length >= limit) break;
    if (typeof value === "string" && value.trim()) parts.push(`${key} ${value.trim()}`);
    else if (typeof value === "number" && Number.isFinite(value)) parts.push(`${key} ${value}`);
    else if (typeof value === "boolean") parts.push(`${key} ${value}`);
  }
  return parts.join(" · ");
}

/** 活动条显示行：单条调用，或连续同名失败的聚合组（count>1 才显示「连续 N 次」）。 */
type StripRow =
  | { kind: "single"; call: ToolCall }
  | { kind: "failedGroup"; tool: string; count: number; call: ToolCall };

/**
 * 连续同名失败聚合：按 order 遍历，连续 failed 且同 tool 的合并为一组。
 * 保留组内**首次**失败作代表（稳定 key + 错误样本）；running/done-degraded 不聚合。
 */
function groupRows(calls: ToolCall[]): StripRow[] {
  const rows: StripRow[] = [];
  for (const call of calls) {
    const prev = rows[rows.length - 1];
    if (call.status === "failed" && prev && prev.kind === "failedGroup" && prev.tool === call.tool) {
      prev.count += 1;
      continue;
    }
    rows.push(call.status === "failed"
      ? { kind: "failedGroup", tool: call.tool, count: 1, call }
      : { kind: "single", call });
  }
  return rows;
}

function Row({ row }: { row: StripRow }) {
  const call = row.call;
  const aggregated = row.kind === "failedGroup" && row.count > 1;
  const degraded = Boolean(call.degraded);
  const failed = call.status === "failed";
  const running = call.status === "running";
  const rateLimited = failed && call.error?.code === RATE_LIMIT_CODE;
  const dot: DotTone = running ? "cinnabar" : failed ? "ochre" : "bamboo";
  const label = toolLabel(call.tool);
  // 聚合行不展开 args/purpose（重试风暴下多行 args 是主要噪音源）
  const desc = aggregated ? "" : call.purpose || argsSummary(call.args);
  const tail = running
    ? "进行中"
    : aggregated
      ? `连续 ${row.count} 次失败`
      : failed
        ? rateLimited
          ? "每日共享配额已用尽"
          : call.data === null || call.data === undefined
            ? "未取得数据"
            : "调用失败"
        : "";

  return (
    <li className={`flex items-center gap-2 text-[12.5px] ${failed || degraded ? "opacity-60" : ""}`.trim()}>
      <PulseDot tone={dot} pulsing={running} />
      <span
        className={`num text-inksoft ${failed ? "line-through decoration-inkfaint/60" : ""}`.trim()}
      >
        {label}
      </span>
      {desc && <span className="text-inkfaint truncate">· {desc}</span>}
      {degraded && <Chip tone="ochre" className="ml-1 shrink-0">降级 · {call.degraded}</Chip>}
      {running ? (
        <span className="text-cinnabar ml-auto text-[11px] shrink-0">{tail}</span>
      ) : failed ? (
        <span className="text-inkfaint ml-auto text-[11px] shrink-0 flex items-center gap-1">
          <IconAlert className="w-3.5 h-3.5 text-ochre" />
          {tail}
        </span>
      ) : (
        <IconCheck className="w-3.5 h-3.5 text-bamboo ml-auto shrink-0" />
      )}
    </li>
  );
}

/** 配额用尽一次性通知卡：配额说明 + 申请自己的免费 API Key（新账号含 5000 次赠送额度）。 */
function QuotaNoticeCard() {
  return (
    <div className="rounded-md border border-ochre/25 bg-ochrewash px-3 py-2.5 text-[12px] leading-relaxed">
      <div className="flex items-center gap-1.5 font-medium text-ochre">
        <IconAlert className="w-3.5 h-3.5 shrink-0" />
        飞猪 API 配额说明
      </div>
      <p className="mt-1 text-inksoft">5000 次赠送额度用完后每日限 100 次免费调用，次日恢复。</p>
      <p className="mt-1 text-inksoft">
        您也可以申请自己的免费 API Key：
        <a
          className="ml-1 underline decoration-ochre/50 underline-offset-2 hover:decoration-ochre"
          href={FLYAI_KEY_URL}
          target="_blank"
          rel="noreferrer"
        >
          flyai.open.fliggy.com
        </a>
      </p>
    </div>
  );
}

/** 工具活动条；过滤后无可见行时不渲染（不留空壳）。 */
export function ToolActivityStrip() {
  const byCallId = useToolsStore((s) => s.byCallId);
  const order = useToolsStore((s) => s.order);
  if (order.length === 0) return null;
  const calls = order
    .map((id) => byCallId[id])
    .filter((c): c is ToolCall => Boolean(c))
    // 只留活的和有问题的：内部状态写入不显示；成功且未降级不显示（结果由 settled 区卡片承担）
    .filter((c) => c.tool !== "update_research_context" && !(c.status === "done" && !c.degraded));
  if (calls.length === 0) return null;
  const quotaExhausted = calls.some((c) => c.status === "failed" && c.error?.code === RATE_LIMIT_CODE);
  const rows = groupRows(calls);
  return (
    <div className="hairline rounded-lg bg-card px-4 py-3 space-y-2">
      <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-1">AGENT 工具活动</div>
      <ul className="space-y-2">
        {rows.map((row) => (
          <Row key={row.call.call_id} row={row} />
        ))}
      </ul>
      {quotaExhausted && <QuotaNoticeCard />}
    </div>
  );
}
