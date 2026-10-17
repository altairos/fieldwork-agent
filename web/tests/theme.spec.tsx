import { beforeEach, describe, expect, test } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  applyTheme,
  isThemeId,
  readStoredTheme,
  THEMES,
  THEME_STORAGE_KEY,
} from "../src/lib/theme";
import { useThemeStore } from "../src/stores/theme";
import { ThemeSwitcher } from "../src/components/ui/ThemeSwitcher";

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  useThemeStore.setState({ theme: "cinnabar" });
});

describe("lib/theme 校验与读写", () => {
  test("isThemeId：四套有效，其余无效", () => {
    for (const id of ["cinnabar", "indigo", "bamboo", "wordblue"]) {
      expect(isThemeId(id)).toBe(true);
    }
    expect(isThemeId("dark")).toBe(false);
    expect(isThemeId(null)).toBe(false);
  });

  test("readStoredTheme：无值/非法值回落默认 cinnabar，有效值原样返回", () => {
    expect(readStoredTheme()).toBe("cinnabar");
    localStorage.setItem(THEME_STORAGE_KEY, "neon");
    expect(readStoredTheme()).toBe("cinnabar");
    localStorage.setItem(THEME_STORAGE_KEY, "wordblue");
    expect(readStoredTheme()).toBe("wordblue");
  });

  test("applyTheme 写 html[data-theme]", () => {
    applyTheme("bamboo");
    expect(document.documentElement.dataset.theme).toBe("bamboo");
  });
});

describe("theme store 三处同步", () => {
  test("setTheme 更新 state + dataset + localStorage", () => {
    useThemeStore.getState().setTheme("indigo");
    expect(useThemeStore.getState().theme).toBe("indigo");
    expect(document.documentElement.dataset.theme).toBe("indigo");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("indigo");
  });
});

describe("ThemeSwitcher 交互", () => {
  test("展开弹层：四套主题按序展示，当前项 aria-checked", () => {
    render(<ThemeSwitcher />);
    const trigger = screen.getByRole("button", { name: "切换界面配色" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const items = screen.getAllByRole("menuitemradio");
    expect(items.map((el) => el.textContent)).toEqual(
      THEMES.map((t) => el2text(t.label, t.note)),
    );
    expect(items[0]).toHaveAttribute("aria-checked", "true");
    expect(items[1]).toHaveAttribute("aria-checked", "false");
  });

  test("选择主题：三处同步 + 弹层关闭", () => {
    render(<ThemeSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "切换界面配色" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /靛青/ }));
    expect(useThemeStore.getState().theme).toBe("indigo");
    expect(document.documentElement.dataset.theme).toBe("indigo");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("indigo");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  test("Escape 关闭弹层", () => {
    render(<ThemeSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "切换界面配色" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  test("点击外部关闭弹层", () => {
    render(<ThemeSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "切换界面配色" }));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

/** menuitemradio 的可访问名 = label + note 文本拼接 */
function el2text(label: string, note: string): string {
  return label + note;
}
