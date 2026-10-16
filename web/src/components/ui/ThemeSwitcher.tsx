import { useEffect, useRef, useState } from "react";
import { THEMES } from "../../lib/theme";
import { useThemeStore } from "../../stores/theme";
import { IconCheck, IconPalette } from "./icons";

/**
 * ThemeSwitcher — 界面配色切换（四套主题：朱砂/靛青/青衿/Word蓝白）。
 * 触发按钮样式与 Header 导出按钮同规格（hairline bg-card h-8）。
 * 弹层：role=menu + menuitemradio（aria-checked）；
 *   选中态 = 实边框 + IconCheck 勾选（交互规范）；
 *   色板预览用 inline style 硬编码 hex（CSS 变量在同一 data-theme 下同色，
 *   无法同屏展示四套，值与 lib/theme.ts 的 swatch 一一对应）；
 *   点击外部 / Escape 关闭；全部项 cursor-pointer（preflight）+ hover +
 *   focus-visible 反馈。
 */
export function ThemeSwitcher() {
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // 点击外部 / Escape 关闭（仅展开时挂监听）
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="切换界面配色"
        onClick={() => setOpen((v) => !v)}
        className="h-8 px-3 rounded-md hairline bg-card text-ink text-[12.5px] hover:bg-cardsunk transition inline-flex items-center gap-1.5 focus-visible:outline-2 focus-visible:outline-cinnabar"
      >
        <IconPalette className="w-4 h-4" />
        <span className="hidden sm:inline">配色</span>
      </button>
      {open && (
        <div
          role="menu"
          aria-label="界面配色"
          className="absolute right-0 mt-2 w-64 z-50 rounded-lg hairline bg-card shadow-pop p-1.5"
        >
          {THEMES.map((t) => {
            const selected = t.id === theme;
            return (
              <button
                key={t.id}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                onClick={() => {
                  setTheme(t.id);
                  setOpen(false);
                }}
                className={`w-full text-left px-2.5 py-2 rounded-md border transition flex items-center gap-2.5 focus-visible:outline-2 focus-visible:outline-cinnabar ${
                  selected
                    ? "border-cinnabar bg-cinnabarwash/60"
                    : "border-transparent hover:bg-cardsunk"
                }`}
              >
                {/* 色板预览：底色 + 主色点（硬编码 hex，见组件头注释） */}
                <span
                  aria-hidden="true"
                  className="w-7 h-7 rounded-md border border-hairline shrink-0 grid place-items-center"
                  style={{ background: t.swatch.base }}
                >
                  <span
                    className="w-2.5 h-2.5 rounded-full"
                    style={{ background: t.swatch.accent }}
                  />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[12.5px] text-ink">{t.label}</span>
                  <span className="block text-[11px] text-inkfaint truncate">{t.note}</span>
                </span>
                {selected && (
                  <IconCheck className="w-4 h-4 text-cinnabar shrink-0" aria-hidden="true" />
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
