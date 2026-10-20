import type { ReactNode } from "react";
import { DEFAULT_PHASES } from "../layout/LeftRail";
import { IconAlert, IconCompass } from "../ui/icons";
import { useProjectStore } from "../../stores/project";
import { useUiStore } from "../../stores/ui";

/**
 * PhaseBanner — 中栏顶部阶段横幅（+ 降级横幅）。
 *
 * 设计板同源：中栏阶段横幅 lines 428-432
 *   容器 line 428 `px-6 py-2.5 bg-cinnabarwash/70 border-b border-cinnabar/15 flex items-center gap-2 text-[12.5px]`
 *   罗盘图标 line 429 `w-4 h-4 text-cinnabar`
 *   阶段主文 line 430 `text-cinnabar font-medium`（「已进入 阶段 C · 行程规划」）
 *   说明尾文 line 431 `text-inkfaint`（「— 行程确认后即可解锁方案生成」）
 *     → 引导规范：尾文优先给解锁条件（PhaseItem.unlock，与设计板原句式同源），
 *       末阶段无解锁目标时回落阶段维度说明 desc。
 * 降级横幅取设计板 ochre 家族（line 265 `bg-ochrewash text-ochre border-ochre/25` + line 132 alert 图标），
 *   承载 ui store 的 `degradedBanner`（error 事件）：如实转述 code/message/hint，用户可关闭。
 *
 * 红线①：横幅只**呈现**阶段与降级，不切 Tab——阶段切换时的右栏面板跳转由 ui store 的
 * phase_transition 分支直接完成（用户反馈：原先是 toast 建议用户手点「查看」）。
 */
export function PhaseBanner() {
  const phase = useProjectStore((s) => s.phase);
  const banner = useUiStore((s) => s.degradedBanner);
  const dismissBanner = useUiStore((s) => s.dismissBanner);

  const meta = DEFAULT_PHASES.find((p) => p.id === phase);
  const title = meta ? `阶段 ${meta.letter} · ${meta.label}` : `阶段 ${phase}`;
  const note = meta
    ? (meta.unlock ? `本阶段条件达成后解锁：${meta.unlock}` : (meta.desc ?? "阶段推进由可验证条件判定"))
    : "阶段推进由可验证条件判定";

  return (
    <>
      <div className="px-6 py-2.5 bg-cinnabarwash/70 border-b border-cinnabar/15 flex items-center gap-2 text-[12.5px]">
        <IconCompass className="w-4 h-4 text-cinnabar shrink-0" />
        <span className="text-cinnabar font-medium shrink-0">{title}</span>
        <span className="text-inkfaint truncate">— {note}</span>
      </div>
      {banner && <DegradedBanner code={banner.code} text={`${banner.message}${banner.hint ? ` · ${banner.hint}` : ""}`} onClose={dismissBanner} />}
    </>
  );
}

/** 降级/错误横幅（ochre 家族，可关闭）。 */
function DegradedBanner({ code, text, onClose }: { code: string; text: ReactNode; onClose: () => void }) {
  return (
    <div className="px-6 py-2 bg-ochrewash border-b border-ochre/25 flex items-center gap-2 text-[12px]">
      <IconAlert className="w-4 h-4 text-ochre shrink-0" />
      <span className="text-inksoft flex-1 min-w-0 truncate">{text}</span>
      <span className="num text-[10.5px] text-inkfaint shrink-0">{code}</span>
      {/* tapzone：触屏热区外扩（批次 B；纯文字钮视觉保持不变） */}
      <button type="button" onClick={onClose} className="tapzone text-[11.5px] text-inkfaint hover:text-ink shrink-0">
        关闭
      </button>
    </div>
  );
}
