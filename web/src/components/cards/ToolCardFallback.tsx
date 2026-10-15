import { IconSpark } from "../ui/icons";
import { CardShell, NoData } from "./CardShell";
import type { ToolCardProps } from "./types";

/**
 * ToolCardFallback — 未注册工具的兜底卡（设计板 lines 464-471 折叠外壳，默认收起省纵向空间）。
 *
 * 契约：`tool_completed.data` 形状未知（内核新增能力时前端尚未适配），故原样 JSON 折叠展示，
 * **不猜字段、不报错、不白屏**——红线②：卡片是唯一同源数据，宁可给原始 payload 也不给臆造结构。
 * 数字不经加工直出（红线③：不做换算、不补 0）。
 */

/** 安全序列化：JSON 不可表示（循环引用/BigInt）时退回 String()，卡片永不抛错。 */
function safeStringify(data: unknown): string {
  try {
    return JSON.stringify(data, null, 2) ?? String(data);
  } catch {
    return String(data);
  }
}

export function ToolCardFallback({ data, degraded }: ToolCardProps) {
  return (
    <CardShell
      title="工具结果"
      subtitle="未适配的结构化视图"
      icon={<IconSpark className="w-4 h-4" />}
      degraded={degraded}
      open={false}
    >
      {data === null || data === undefined ? (
        <NoData hint="该工具本次未返回数据（失败或降级）；详见头部标记与助手说明。" />
      ) : (
        <pre className="num text-[11.5px] leading-relaxed text-inksoft bg-cardsunk hairline rounded-md p-3 overflow-x-auto whitespace-pre-wrap break-all">
          {safeStringify(data)}
        </pre>
      )}
    </CardShell>
  );
}
