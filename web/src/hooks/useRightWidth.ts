import { useEffect, useState } from "react";
import { loadRightWidth, RIGHT_W_KEY } from "../components/layout/RightResizeHandle";

/**
 * 右栏宽度（评审 R8 拆分自 App.tsx 的 Workspace）。
 * 宽度必须由**布局 grid 的祖先**持有（`--right-w` CSS 变量设在 grid 上），
 * 所以状态不能下沉到把手组件；把手只上抛新值（clamp 由它保证在界内）。
 * 一变即写 localStorage，刷新/重挂载由 loadRightWidth 恢复。
 */
export function useRightWidth() {
  const [rightWidth, setRightWidth] = useState(loadRightWidth);
  useEffect(() => {
    localStorage.setItem(RIGHT_W_KEY, String(rightWidth));
  }, [rightWidth]);
  return { rightWidth, setRightWidth };
}
