/**
 * 四阶段配置（纯数据，零依赖）— `stage id → 字母 / 名称 / 简介 / 解锁后阶段`。
 *
 * 原先定义在 `components/layout/LeftRail.tsx`，被 `stores/ui.ts` 反向 import（为了把
 * 阶段 id 翻译成中文名做「已进入「行程规划」阶段」的提示）。于是**状态层依赖了组件层**：
 * 单测导入 ui store 就会连带加载左栏与图标库。评审 P1-3 把工具元数据下沉的同时，
 * 顺手把这份表也挪出来——阶段表本来就是与后端 phase 值对齐的业务数据，不属于任何组件。
 *
 * 组件侧（LeftRail / Header / PhaseBanner / ProjectHub）与状态侧都从这里读，单一来源。
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
  /** 本阶段达成后解锁的下一阶段（末阶段无；PhaseBanner 解锁尾文用） */
  unlock?: string;
}

/** 四阶段默认配置（名称/简介取自设计板 lines 373-376、405-417） */
export const DEFAULT_PHASES: PhaseItem[] = [
  { id: "research_positioning", letter: "A", label: "研究定位", desc: "学科·问题·规模·经费·时间窗", unlock: "田野点画像" },
  { id: "field_site_profiles", letter: "B", label: "田野点画像", desc: "交通·住宿·文化·配套四维", unlock: "行程规划" },
  { id: "itinerary_planning", letter: "C", label: "行程规划", desc: "节奏·缓冲·预算·备选路线", unlock: "方案生成" },
  { id: "proposal_generation", letter: "D", label: "方案生成", desc: "英美式八章方案草稿" },
];

/** 阶段 id → 中文名（未知 id 回落原值，绝不抛错）。 */
export function phaseLabel(id: string): string {
  return DEFAULT_PHASES.find((p) => p.id === id)?.label ?? id;
}
