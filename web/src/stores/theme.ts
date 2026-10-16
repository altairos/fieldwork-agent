import { create } from "zustand";
import { applyTheme, readStoredTheme, THEME_STORAGE_KEY, type ThemeId } from "../lib/theme";

/** 界面配色 store：初始值取 localStorage（index.html FOUC 脚本已先行
 * 设置过 html[data-theme]，此处不重复 apply）；setTheme 时同步
 * DOM data-theme + localStorage + state 三处。 */
interface ThemeState {
  theme: ThemeId;
  setTheme: (t: ThemeId) => void;
}

export const useThemeStore = create<ThemeState>()((set) => ({
  theme: readStoredTheme(),
  setTheme: (t) => {
    applyTheme(t);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, t);
    } catch {
      // localStorage 不可用（隐私模式等）：仅本次会话生效，不阻断切换
    }
    set({ theme: t });
  },
}));
