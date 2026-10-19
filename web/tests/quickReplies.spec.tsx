import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QuickReplies } from "../src/components/chat/QuickReplies";

/**
 * QuickReplies 的选中态按下标对齐 groups（评审 R7）。组件原先只在「提交后 groups 清空 →
 * 卸载」这条路径上复位；一旦出现「非空替换但不卸载」（刷新回填/重放对齐/连续两问），
 * 下标错位会把甲题的答案标到乙题上，并把旧选项拼进新的答案里发出去。
 */

const G = (label: string, options: string[]) => ({ label, options });

describe("QuickReplies 选项状态与 groups 对齐（R7）", () => {
  it("groups 内容变了即清空选中，不会把旧选项带进新答案", () => {
    const onSend = vi.fn();
    const { rerender } = render(
      <QuickReplies groups={[G("问题一", ["A", "B"])]} onSend={onSend} onCustom={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "A" }));
    expect(screen.getByRole("button", { name: "A" })).toHaveAttribute("aria-pressed", "true");

    rerender(<QuickReplies groups={[G("问题二", ["C", "D"])]} onSend={onSend} onCustom={() => {}} />);
    expect(screen.getByRole("button", { name: "C" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("quick-reply-send")).toBeDisabled();   // 没有任何选中
  });

  it("同内容重渲染不误清用户的选择", () => {
    const onSend = vi.fn();
    const groups = [G("问题", ["A", "B"])];
    const { rerender } = render(<QuickReplies groups={groups} onSend={onSend} onCustom={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "A" }));
    rerender(<QuickReplies groups={[G("问题", ["A", "B"])]} onSend={onSend} onCustom={() => {}} />);
    expect(screen.getByRole("button", { name: "A" })).toHaveAttribute("aria-pressed", "true");
  });

  it("多组选中按「标签：选项」拼成一条消息", () => {
    const onSend = vi.fn();
    render(
      <QuickReplies
        groups={[G("经费", ["2 万", "5 万"]), G("天数", ["7 天", "10 天"])]}
        onSend={onSend}
        onCustom={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "5 万" }));
    fireEvent.click(screen.getByRole("button", { name: "10 天" }));
    fireEvent.click(screen.getByTestId("quick-reply-send"));
    expect(onSend).toHaveBeenCalledWith("经费：5 万\n天数：10 天");
  });
});
