import type { ReactNode } from "react";

/**
 * Card — 卡片容器。
 * 设计板同源：`.hairline rounded-lg bg-card` + shadow-card（line 289 新建卡、line 319 项目卡）；
 * sunk → bg-cardsunk 内凹（line 611 summary_hints、色板 line 172「代码/内凹」），内凹不带阴影。
 */
export function Card({
  sunk = false,
  children,
  className = "",
}: {
  sunk?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const skin = sunk
    ? "hairline rounded-lg bg-cardsunk"
    : "hairline rounded-lg bg-card shadow-card";
  return <div className={`${skin} ${className}`.trim()}>{children}</div>;
}
