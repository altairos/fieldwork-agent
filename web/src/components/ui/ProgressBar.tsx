/**
 * ProgressBar — 四段进度条（阶段 A/B/C/D）。
 * 设计板同源：view-hub 项目卡四段条（line 330/344/357）：
 *   容器 `flex gap-1`；每段 `h-1 flex-1 rounded-full`；
 *   已完成 bg-bamboo、进行中 bg-cinnabar（卡1 当前段 C 朱砂）、未到 bg-hairline。
 * 段下附 num 小标签（复核用，数字/编号走 Fraunces — 设计红线②）。
 * props.phase = 当前段索引（0 基）；之前的段视为已完成。
 */
export function ProgressBar({
  phase,
  segments,
  className = "",
}: {
  /** 当前段索引（0 基） */
  phase: number;
  /** 段标签（如 ["A","B","C","D"]） */
  segments: string[];
  className?: string;
}) {
  return (
    <div className={className.trim() || undefined}>
      <div className="flex gap-1" role="progressbar" aria-valuemin={0} aria-valuemax={segments.length} aria-valuenow={phase + 1}>
        {segments.map((seg, i) => {
          const bar =
            i < phase ? "bg-bamboo" : i === phase ? "bg-cinnabar" : "bg-hairline";
          return <span key={seg} className={`h-1 flex-1 rounded-full ${bar}`} />;
        })}
      </div>
      <div className="flex gap-1 mt-1">
        {segments.map((seg, i) => (
          <span
            key={seg}
            className={`num text-[10px] flex-1 text-center ${
              i === phase ? "text-cinnabar" : "text-inkfaint"
            }`}
          >
            {seg}
          </span>
        ))}
      </div>
    </div>
  );
}
