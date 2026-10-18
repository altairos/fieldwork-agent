/** 界面配色主题：六套可选皮肤（2026-10-16 评审后落地四套，2026-10-18 追加
 * 素笺/缃帙，见 docs/reviews/2026-10-16-前端设计评审.md 第四节）。
 *
 * 色值本体在 styles/themes.css（html[data-theme] CSS 变量）；
 * 本模块只负责元数据、localStorage 读写（沿用 lib/keys.ts 的
 * "fieldwork." 前缀惯例）与 document.documentElement.dataset.theme 设置。
 * index.html 的内联脚本在首帧渲染前完成同一件事（防 FOUC），
 * main.tsx 再调 applyTheme 双保险（jsdom / 脚本被禁等场景）。
 */

export type ThemeId =
  | "cinnabar"
  | "indigo"
  | "bamboo"
  | "wordblue"
  | "obsidian"
  | "vscode";

export const THEME_STORAGE_KEY = "fieldwork.theme";

export interface ThemeMeta {
  id: ThemeId;
  /** 名称（弹层主标题） */
  label: string;
  /** 一句话气质说明（弹层副标题） */
  note: string;
  /** 色板预览（inline style 用硬编码 hex：CSS 变量在同一 data-theme 下
   *  同色，无法同屏展示六套，故预览值与 themes.css 各套主色一一对应） */
  swatch: { base: string; accent: string };
}

/** 六套主题（顺序即展示顺序，cinnabar 为默认）；obsidian/vscode 为技术参照 id，
 *  UI 只展示 label（素笺仿 Obsidian 灰白、缥囊仿青白丝绸、缃帙仿 VS Code 赭黄）
 */
export const THEMES: ThemeMeta[] = [
  {
    id: "cinnabar",
    label: "朱砂",
    note: "学术档案 · 宣纸米白 + 朱砂点睛（默认）",
    swatch: { base: "#F9F6F0", accent: "#A8412E" },
  },
  {
    id: "indigo",
    label: "靛青",
    note: "一色克制 · 主色与数据色统一为靛青",
    swatch: { base: "#F9F6F0", accent: "#3A5C74" },
  },
  {
    id: "wordblue",
    label: "Word 蓝白",
    note: "工具习惯 · Fluent 蓝 + 纯白底",
    swatch: { base: "#FFFFFF", accent: "#2B579A" },
  },
  {
    id: "obsidian",
    label: "素笺",
    note: "素笺墨影 · 灰白纸面 + 烟墨点睛",
    swatch: { base: "#FAFAFA", accent: "#4A4A4A" },
  },
  {
    id: "bamboo",
    label: "缥囊",
    note: "缥囊素丝 · 淡青纸面 + 白丝光泽",
    swatch: { base: "#F4F9F6", accent: "#3A7661" },
  },
  {
    id: "vscode",
    label: "缃帙",
    note: "青灯黄卷 · 赭黄工作台 + 米纸正文",
    swatch: { base: "#FCF9F2", accent: "#7E5E16" },
  },
];

export function isThemeId(v: unknown): v is ThemeId {
  return (
    v === "cinnabar" ||
    v === "indigo" ||
    v === "bamboo" ||
    v === "wordblue" ||
    v === "obsidian" ||
    v === "vscode"
  );
}

/** 读 localStorage；无值/非法值一律回落默认 cinnabar（不抛错）。 */
export function readStoredTheme(): ThemeId {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeId(v) ? v : "cinnabar";
  } catch {
    return "cinnabar"; // localStorage 不可用（隐私模式等）按默认
  }
}

/** 写 html[data-theme]（themes.css 选择器入口）。 */
export function applyTheme(theme: ThemeId): void {
  document.documentElement.dataset.theme = theme;
}
