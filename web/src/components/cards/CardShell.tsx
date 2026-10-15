import type { ReactNode } from "react";
import { Chip } from "../ui/Chip";
import { EmptyState } from "../ui/EmptyState";
import { IconAlert, IconCaret } from "../ui/icons";

/**
 * CardShell — 工具结果卡的统一折叠外壳（卡片层内部共享件，不进注册表）。
 *
 * 设计板同源：中栏工具结果卡 lines 464-471
 *   `<details open class="hairline rounded-lg bg-card overflow-hidden shadow-card">`
 *   + `<summary class="px-4 py-2.5 flex items-center gap-2 bg-cardsunk/60 border-b border-hairline">`
 *   + caret(line 467 `#ic-caret`，开合旋转见 tokens.css `.caret`) + 工具图标(line 468)
 *   + 标题(line 469 `text-[13px] font-medium`) + 状态 chip(line 470 `ml-auto`)；
 *   内容区 line 472 `p-4`。降级 chip 取 line 447「降级 · flyai_unavailable」（ochre tone）。
 *
 * title = 工具语义名，subtitle = 关键标识（地点/区间/天数），两者分列独立文本节点，
 * 便于时间线扫读，也便于测试与读屏定位。
 */
export function CardShell({
  title,
  subtitle,
  icon,
  degraded,
  statusChip,
  open = true,
  children,
}: {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  /** 内核 Envelope.degraded（如 "flyai_unavailable"）；有值即挂 ochre 降级 chip */
  degraded?: string | null;
  statusChip?: ReactNode;
  /** 默认展开（设计板 line 465 `open`）；嵌套/次要内容可传 false 收起省纵向空间 */
  open?: boolean;
  children: ReactNode;
}) {
  return (
    <details open={open} className="hairline rounded-lg bg-card overflow-hidden shadow-card">
      <summary className="px-4 py-2.5 flex items-center gap-2 bg-cardsunk/60 border-b border-hairline">
        <IconCaret className="w-4 h-4 text-cinnabar caret shrink-0" />
        {icon && <span className="w-4 h-4 text-inksoft shrink-0">{icon}</span>}
        <span className="text-[13px] font-medium text-ink shrink-0">{title}</span>
        {subtitle && <span className="text-[11.5px] text-inkfaint truncate">{subtitle}</span>}
        <span className="ml-auto flex items-center gap-1.5 shrink-0">
          {degraded && <Chip tone="ochre">降级 · {degraded}</Chip>}
          {statusChip}
        </span>
      </summary>
      <div className="p-4">{children}</div>
    </details>
  );
}

/**
 * Notice — 卡内提示条（降级说明/待核验/超支警示）。
 * 配色沿用设计板 chip tone 家族（lines 234-236/265）与红线横幅（line 270-271）：
 *   ochre  → 赭黄：降级、缺报价、待人工核验（line 265/487 缓冲日语义）
 *   danger → 超支红警（line 255-256 BudgetBar 超支态）
 *   neutral→ 中性说明（cardsunk 内凹，line 611）
 */
export function Notice({
  tone = "ochre",
  children,
  className = "",
}: {
  tone?: "ochre" | "danger" | "neutral";
  children: ReactNode;
  className?: string;
}) {
  const skin =
    tone === "danger"
      ? "bg-cinnabarwash border-danger/25 text-cinnabar"
      : tone === "neutral"
        ? "bg-cardsunk border-hairline text-inksoft"
        : "bg-ochrewash border-ochre/25 text-ochre";
  return (
    <div
      className={`rounded-md border p-2.5 text-[12px] leading-relaxed flex items-start gap-2 ${skin} ${className}`.trim()}
    >
      <IconAlert className="w-4 h-4 shrink-0 mt-px" />
      <span className="text-inksoft flex-1 min-w-0">{children}</span>
    </div>
  );
}

/**
 * NoData — `data` 为 null/undefined（工具失败或降级未返回数据）时的统一兜底，
 * 保证卡片永不白屏、永不抛错（设计红线②：卡片是唯一同源数据，缺数据要如实说明）。
 */
export function NoData({ hint }: { hint?: string }) {
  return (
    <EmptyState
      icon={<IconAlert className="w-4 h-4" />}
      title="工具未返回数据"
      hint={hint ?? "本次调用失败或降级，无可展示的结果；请查看头部标记与助手说明。"}
    />
  );
}
