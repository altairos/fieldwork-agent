import { useState } from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Tabs, tabIds } from "../src/components/ui/Tabs";

/**
 * Tabs 的 WAI-ARIA tabs pattern 三件事（评审 R6）：
 * 1. aria-controls 指向 tabpanel（id 由 tabIds 唯一来源生成，两侧不可能拼错）；
 * 2. roving tabindex：只有选中项 Tab 可达，其余 -1（Tab 键只在 tablist 停一次）；
 * 3. 方向键切换 + 焦点跟随 + 首尾循环，Home/End 跳首尾。
 * 红点仍为 aria-hidden 装饰。
 */

const TABS = [
  { id: "chat", label: "对话" },
  { id: "evidence", label: "检索结果", dot: true },
];
const IDS = tabIds("middle");

function Harness() {
  const [active, setActive] = useState("chat");
  return <Tabs tabs={TABS} active={active} onChange={setActive} cols={2} idBase="middle" />;
}

describe("Tabs 无障碍（R6）", () => {
  it("tab 与 tabpanel 的 id 契约一致", () => {
    render(<Harness />);
    const chat = screen.getByRole("tab", { name: /对话/ });
    expect(chat).toHaveAttribute("id", IDS.tab("chat"));
    expect(chat).toHaveAttribute("aria-controls", IDS.panel("chat"));
    expect(chat).toHaveAttribute("aria-selected", "true");
  });

  it("roving tabindex：选中项 0，其余 -1", () => {
    render(<Harness />);
    expect(screen.getByRole("tab", { name: /对话/ })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: /检索结果/ })).toHaveAttribute("tabindex", "-1");
  });

  it("方向键切换选中且焦点跟随，右端循环回首", () => {
    render(<Harness />);
    fireEvent.keyDown(screen.getByRole("tab", { name: /对话/ }), { key: "ArrowRight" });
    const evidence = screen.getByRole("tab", { name: /检索结果/ });
    expect(evidence).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(evidence);

    fireEvent.keyDown(evidence, { key: "ArrowRight" });     // 循环
    const chat = screen.getByRole("tab", { name: /对话/ });
    expect(chat).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(chat);

    fireEvent.keyDown(chat, { key: "ArrowLeft" });          // 反向循环
    expect(screen.getByRole("tab", { name: /检索结果/ })).toHaveAttribute("aria-selected", "true");
  });

  it("Home/End 跳首尾", () => {
    render(<Harness />);
    const chat = screen.getByRole("tab", { name: /对话/ });
    fireEvent.keyDown(chat, { key: "End" });
    expect(screen.getByRole("tab", { name: /检索结果/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: /检索结果/ }), { key: "Home" });
    expect(screen.getByRole("tab", { name: /对话/ })).toHaveAttribute("aria-selected", "true");
  });
});
