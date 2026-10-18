import { useState } from "react";
import { Chip } from "../ui/Chip";
import { IconAlert, IconCaret } from "../ui/icons";
import { getToolCard } from "../cards/registry";
import { toolLabel } from "../cards/toolLabels";

/**
 * CollapsedToolCard — 过程性检索工具的折叠摘要条（C 节问题 2，对话流精简）。
 *
 * toolVisibility 分级为 "collapsed" 的工具（8 搜索原语）在时间线 settled 区不再
 * 平铺整卡（生产会话曾出现 15KB search_keyword 原始结果刷屏），改为单行摘要：
 * 工具显示名 + 结果条数与价格区间（ItemPage 形状容错解析）；失败态显示错误
 * message（ochre）；degraded 徽章保留。点击展开渲染注册表原卡（组件内 state，
 * 再点收起）——信息不丢失，密度下降。
 *
 * 红线沿袭卡片层：数字直出不加工（total/cheapest/dearest 原样），解析不出
 * ItemPage 形状时回落「无数据」，绝不臆造。
 */

/** ItemPage 形状容错解析（kernel/models.py：{items, total, cheapest, dearest}）。 */
function parseItemPage(data: unknown): { total: number; cheapest: number | null; dearest: number | null } | null {
  if (!data || typeof data !== "object") return null;
  const d = data as { total?: unknown; cheapest?: unknown; dearest?: unknown };
  if (typeof d.total !== "number") return null;
  return {
    total: d.total,
    cheapest: typeof d.cheapest === "number" ? d.cheapest : null,
    dearest: typeof d.dearest === "number" ? d.dearest : null,
  };
}

/** 摘要文本：`N 条 · ¥low–high`；total=0 或形状不识别 → 「无数据」（价格缺省只报条数）。 */
function summaryText(data: unknown): string {
  const page = parseItemPage(data);
  if (!page || page.total === 0) return "无数据";
  let text = `${page.total} 条`;
  if (page.cheapest !== null && page.dearest !== null) text += ` · ¥${page.cheapest}–${page.dearest}`;
  else if (page.cheapest !== null) text += ` · ¥${page.cheapest} 起`;
  return text;
}

export function CollapsedToolCard({
  tool,
  data,
  degraded,
  error,
}: {
  tool: string;
  data: unknown;
  degraded?: string | null;
  /** tools store 的 ToolErrorInfo（失败时）；仅取 message 展示。 */
  error?: { message: string } | null;
}) {
  const [open, setOpen] = useState(false);
  const label = toolLabel(tool);

  return (
    <div data-testid="collapsed-tool-card">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="w-full hairline rounded-lg bg-card px-3 py-2 flex items-center gap-2 text-[12.5px] text-left hover:bg-cardsunk transition"
      >
        <IconCaret className={`w-3.5 h-3.5 shrink-0 text-inkfaint transition-transform ${open ? "rotate-90" : ""}`} />
        <span className="shrink-0 text-inksoft">{label}</span>
        {error ? (
          <span className="min-w-0 flex-1 flex items-center gap-1 text-ochre truncate">
            <IconAlert className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate">{error.message}</span>
          </span>
        ) : (
          <span className="min-w-0 flex-1 text-inkfaint truncate">{summaryText(data)}</span>
        )}
        {degraded && <Chip tone="ochre" className="shrink-0">降级 · {degraded}</Chip>}
      </button>
      {open && (() => {
        const Card = getToolCard(tool);
        return <Card data={data === undefined ? null : data} degraded={degraded ?? null} />;
      })()}
    </div>
  );
}
