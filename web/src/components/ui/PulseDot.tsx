/**
 * PulseDot — 脉冲点（工具进行中/状态指示）。
 * 设计板同源：line 80 `.dot`（6px 圆点）+ 工具活动条（lines 263-265）：
 *   完成 bg-bamboo、进行中 bg-cinnabar animate-pulse（line 264）、降级 bg-ochre、失效 bg-inkfaint。
 * pulsing=true 时加 animate-pulse（进行中态）。
 */
export type DotTone = "cinnabar" | "indigo" | "bamboo" | "ochre" | "neutral";

const TONE: Record<DotTone, string> = {
  cinnabar: "bg-cinnabar",
  indigo: "bg-indigo",
  bamboo: "bg-bamboo",
  ochre: "bg-ochre",
  neutral: "bg-inkfaint",
};

export function PulseDot({
  tone = "cinnabar",
  pulsing = false,
  className = "",
}: {
  tone?: DotTone;
  pulsing?: boolean;
  className?: string;
}) {
  return (
    <span
      className={`dot ${TONE[tone]} ${pulsing ? "animate-pulse" : ""} ${className}`.trim()}
      aria-hidden="true"
    />
  );
}
