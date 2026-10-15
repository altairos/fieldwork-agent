import type { ReactNode } from "react";

/**
 * Chip — 圆角小标签（学科/模板/状态）。
 * 设计板同源：line 81 `.chip` 基座 + 各 tone 配色（lines 234-236, 265, 294-305, 602）。
 * tone 映射（wash 底 + 主色文 + 主色 25%/20% 描边）：
 *   cinnabar → bg-cinnabarwash text-cinnabar border-cinnabar/25（line 235/294）
 *   indigo   → bg-indigowash text-indigo border-indigo/20（line 303）
 *   bamboo   → bg-bamboowash text-bamboo border-bamboo/25（line 234）
 *   ochre    → bg-ochrewash text-ochre border-ochre/25（line 265）
 *   neutral  → bg-cardsunk text-inksoft border-hairline（line 236/641）
 */
export type ChipTone = "cinnabar" | "indigo" | "bamboo" | "ochre" | "neutral";

const TONE: Record<ChipTone, string> = {
  cinnabar: "bg-cinnabarwash text-cinnabar border-cinnabar/25",
  indigo: "bg-indigowash text-indigo border-indigo/20",
  bamboo: "bg-bamboowash text-bamboo border-bamboo/25",
  ochre: "bg-ochrewash text-ochre border-ochre/25",
  neutral: "bg-cardsunk text-inksoft border-hairline",
};

export function Chip({
  tone = "neutral",
  children,
  className = "",
}: {
  tone?: ChipTone;
  children: ReactNode;
  className?: string;
}) {
  return <span className={`chip ${TONE[tone]} ${className}`.trim()}>{children}</span>;
}
