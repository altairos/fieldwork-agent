import type { ReactNode } from "react";

/**
 * SectionTitle — 分节标题（宋体 + 朱砂竖条 + 可选编号）。
 * 设计板同源：设计系统页 H2（line 166/188/201/229）：
 *   `font-serif text-[18px] font-semibold mb-3 flex items-center gap-2`
 *   前置 `<span class="w-1.5 h-4 bg-cinnabar rounded-sm">` 朱砂竖条。
 * index 提供时以 num 朱砂编号呈现（参照 line 373 `num text-cinnabar font-600` 的编号用法）。
 */
export function SectionTitle({
  index,
  children,
  className = "",
}: {
  index?: string | number;
  children: ReactNode;
  className?: string;
}) {
  return (
    <h2 className={`font-serif text-[18px] font-semibold mb-3 flex items-center gap-2 ${className}`.trim()}>
      <span className="w-1.5 h-4 bg-cinnabar rounded-sm" aria-hidden="true" />
      {index !== undefined && <span className="num text-cinnabar">{index}</span>}
      {children}
    </h2>
  );
}
