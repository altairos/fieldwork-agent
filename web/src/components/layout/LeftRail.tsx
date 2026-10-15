import { IconCheck, IconCompass, IconLock } from "../ui/icons";

/**
 * LeftRail — 左栏阶段导航（PhaseRail）。
 * 设计板同源：workspace 左栏 lines 392-423：
 *   容器 `bg-paperdeep border-r border-hairline flex flex-col`（line 393）
 *   节标签「访谈阶段」`px-3 py-3 text-[10.5px] tracking-[.18em] text-inkfaint`（line 401）
 *   条目基座 `flex items-start gap-2.5 p-2.5 rounded-md`（lines 403-418）：
 *     已完成 → 竹青圆标+check，文字 text-inksoft，hover:bg-card/70（line 403-406）
 *     进行中 → `bg-card border border-cinnabar/30 shadow-card` + 朱砂圆标 num 字母（line 411-413）
 *     未解锁 → `opacity-55` + 灰圆标 lock（line 415-417）
 *   底部「返回项目集」按钮（line 421）。
 */
export interface PhaseItem {
  /** 阶段 id（对齐后端 phase：research_positioning 等） */
  id: string;
  /** 阶段字母 A/B/C/D */
  letter: string;
  /** 阶段名 */
  label: string;
  /** 简介（设计板 lines 373-376 四阶段访谈说明） */
  desc: string;
}

/** 四阶段默认配置（名称/简介取自设计板 lines 373-376、405-417） */
export const DEFAULT_PHASES: PhaseItem[] = [
  { id: "research_positioning", letter: "A", label: "研究定位", desc: "学科·问题·规模·经费·时间窗" },
  { id: "field_site_profiles", letter: "B", label: "田野点画像", desc: "交通·住宿·文化·配套四维" },
  { id: "itinerary_planning", letter: "C", label: "行程规划", desc: "节奏·缓冲·预算·备选路线" },
  { id: "proposal_generation", letter: "D", label: "方案生成", desc: "八章 Research Proposal" },
];

export function LeftRail({
  phase,
  phases = DEFAULT_PHASES,
  onSelect,
  onBack,
}: {
  /** 当前阶段 id */
  phase: string;
  phases?: PhaseItem[];
  onSelect?: (id: string) => void;
  onBack?: () => void;
}) {
  const current = phases.findIndex((p) => p.id === phase);
  return (
    <div className="bg-paperdeep border-r border-hairline flex flex-col min-h-0">
      <div className="px-3 py-3 text-[10.5px] tracking-[.18em] text-inkfaint">访谈阶段</div>
      <nav className="px-3 space-y-1 flex-1 overflow-y-auto scroll">
        {phases.map((p, i) => {
          const done = i < current;
          const active = i === current;
          const locked = i > current;
          const rowBase = "w-full text-left flex items-start gap-2.5 p-2.5 rounded-md transition";
          const rowSkin = active
            ? "bg-card border border-cinnabar/30 shadow-card"
            : locked
              ? "opacity-55 hover:bg-card/70"
              : "hover:bg-card/70";
          const dotBase = "w-5 h-5 grid place-items-center rounded-full shrink-0 mt-0.5";
          const dotSkin = active
            ? "bg-cinnabar text-paper num text-[11px]"
            : done
              ? "bg-bamboo text-paper"
              : "bg-hairline text-inksoft";
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => onSelect?.(p.id)}
              aria-current={active ? "step" : undefined}
              className={`${rowBase} ${rowSkin}`}
            >
              <span className={`${dotBase} ${dotSkin}`}>
                {done ? <IconCheck className="w-3 h-3" /> : locked ? <IconLock className="w-3 h-3" /> : p.letter}
              </span>
              <span>
                <span
                  className={`text-[13px] block ${
                    active ? "text-cinnabar font-medium" : "text-inksoft"
                  }`}
                >
                  {p.letter} · {p.label}
                </span>
                <span className="text-[10.5px] text-inkfaint">{p.desc}</span>
              </span>
            </button>
          );
        })}
      </nav>
      {onBack && (
        <div className="p-3 border-t border-hairline">
          <button
            type="button"
            onClick={onBack}
            className="w-full h-9 rounded-md hairline bg-card text-[12.5px] text-inksoft hover:bg-cardsunk transition flex items-center justify-center gap-1.5"
          >
            <IconCompass className="w-4 h-4" />
            返回项目集
          </button>
        </div>
      )}
    </div>
  );
}
