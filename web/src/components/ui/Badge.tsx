import { IconCheck } from "./icons";

/**
 * Badge — 阶段徽标（A/B/C/D 四段圆形小徽标）。
 * 设计板同源：PhaseRail 阶段圆标（lines 404/412/416）：
 *   active   → bg-cinnabar text-paper num（line 412，进行中朱砂）
 *   done     → bg-bamboo text-paper + check 图标（line 404，已完成竹青）
 *   inactive → bg-hairline text-inksoft（line 416，未解锁灰显）
 */
export function Badge({
  phase,
  active = false,
  done = false,
  className = "",
}: {
  /** 阶段字母（A/B/C/D） */
  phase: string;
  active?: boolean;
  done?: boolean;
  className?: string;
}) {
  const base = "w-5 h-5 grid place-items-center rounded-full shrink-0";
  const skin = active
    ? "bg-cinnabar text-paper num text-[11px]"
    : done
      ? "bg-bamboo text-paper"
      : "bg-hairline text-inksoft";
  return (
    <span className={`${base} ${skin} ${className}`.trim()} aria-current={active ? "step" : undefined}>
      {done && !active ? <IconCheck className="w-3 h-3" /> : phase}
    </span>
  );
}
