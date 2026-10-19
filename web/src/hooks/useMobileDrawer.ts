import { useEffect } from "react";
import { useUiStore } from "../stores/ui";

/**
 * 移动端抽屉/底栏（评审 R8 拆分自 App.tsx 的 Workspace）。
 * 桌面（lg+）三栏常驻，这两个状态不参与布局——断点由 CSS 控制，JS 只管移动形态。
 * Escape 关闭左栏抽屉，对齐 ThemeSwitcher 的键盘关闭惯例；桌面无抽屉，监听为空转。
 */
export function useMobileDrawer() {
  const leftCollapsed = useUiStore((s) => s.leftCollapsed);
  const rightCollapsed = useUiStore((s) => s.rightCollapsed);
  const toggleLeft = useUiStore((s) => s.toggleLeft);
  const toggleRight = useUiStore((s) => s.toggleRight);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !useUiStore.getState().leftCollapsed) toggleLeft();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toggleLeft]);

  return { leftCollapsed, rightCollapsed, toggleLeft, toggleRight };
}
