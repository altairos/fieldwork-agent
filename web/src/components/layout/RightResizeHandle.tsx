import { useEffect, useRef, useState } from "react";

/** 右栏宽度持久化键与边界（P3）：--right-w CSS 变量驱动 App 布局 grid 第三列。 */
export const RIGHT_W_KEY = "fieldwork.rightWidth";
export const RIGHT_W_MIN = 280;
export const RIGHT_W_MAX = 560;
export const RIGHT_W_DEFAULT = 340;

export function clampRightWidth(w: number): number {
  return Math.min(RIGHT_W_MAX, Math.max(RIGHT_W_MIN, w));
}

/** 恢复上次宽度：localStorage 缺失/越界/非数 → 默认 340（原固定列宽）。 */
export function loadRightWidth(): number {
  const saved = Number(localStorage.getItem(RIGHT_W_KEY));
  return Number.isFinite(saved) && saved >= RIGHT_W_MIN && saved <= RIGHT_W_MAX
    ? saved
    : RIGHT_W_DEFAULT;
}

/**
 * RightResizeHandle — 桌面右栏左边缘的拖拽调宽把手（P3）。
 *
 * pointerdown 记起点，拖拽期间挂 window 级 pointermove/pointerup（不依赖
 * pointer capture，越过把手本体也能继续拖），Δx 反向调宽（向左拖变宽），
 * clamp 到 [280, 560]；松手卸载监听。宽度状态由 App 持有（CSS 变量须设在
 * grid 祖先容器上，子组件作用域够不着），本组件只上抛新值。
 */
export function RightResizeHandle({
  width,
  onResize,
}: {
  width: number;
  onResize: (w: number) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const startRef = useRef({ startX: 0, startW: 0 });

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => {
      const { startX, startW } = startRef.current;
      onResize(clampRightWidth(startW + (startX - e.clientX)));
    };
    const onUp = () => setDragging(false);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [dragging, onResize]);

  return (
    <div
      role="separator"
      aria-label="调整右栏宽度"
      aria-orientation="vertical"
      title="拖拽调整右栏宽度"
      onPointerDown={(e) => {
        startRef.current = { startX: e.clientX, startW: width };
        setDragging(true);
      }}
      className={`hidden lg:block absolute left-0 inset-y-0 z-10 w-1.5 -translate-x-1/2 touch-none cursor-col-resize hover:bg-cinnabar/25 ${
        dragging ? "bg-cinnabar/25" : ""
      }`}
    />
  );
}
