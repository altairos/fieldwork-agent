import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useStickBottom } from "../src/hooks/useStickBottom";

/**
 * useStickBottom — 中栏两 tab 的贴底行为（用户反馈：切 tab 回来落在最上面）。
 * jsdom 无布局：把 scrollTop / scrollHeight / clientHeight 三个属性整体接管，
 * 让「容器可视区」变成确定值（内容 500px / 视口 200px），断言才可复现。
 */

const SCROLL_HEIGHT = 500;
const CLIENT_HEIGHT = 200;

function Box({ children }: { children?: ReactNode }) {
  const scroll = useStickBottom<HTMLDivElement>();
  return (
    <div data-testid="box" ref={scroll.ref} onScroll={scroll.onScroll}>
      {children}
    </div>
  );
}

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get() { return SCROLL_HEIGHT; },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get() { return CLIENT_HEIGHT; },
  });
  Object.defineProperty(HTMLElement.prototype, "scrollTop", {
    configurable: true,
    get() { return (this as unknown as { _st?: number })._st ?? 0; },
    set(v: number) { (this as unknown as { _st?: number })._st = v; },
  });
});

afterEach(() => {
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollHeight;
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight;
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollTop;
});

describe("useStickBottom", () => {
  test("挂载即贴底（切 tab 回来不再从最上面开始翻）", () => {
    render(<Box>历史消息</Box>);
    expect(screen.getByTestId("box").scrollTop).toBe(SCROLL_HEIGHT);
  });

  test("停在底部时新内容继续跟随", async () => {
    const { rerender } = render(<Box>旧内容</Box>);
    const box = screen.getByTestId("box");
    box.scrollTop = 0;                                  // 观察者触发前先归零，验证会被拉回
    rerender(<Box>新内容</Box>);
    await waitFor(() => expect(box.scrollTop).toBe(SCROLL_HEIGHT));
  });

  test("用户上滚阅读时不打扰；滚回底部后恢复跟随", async () => {
    const { rerender } = render(<Box>旧内容</Box>);
    const box = screen.getByTestId("box");
    box.scrollTop = 0;
    fireEvent.scroll(box);                              // 距底 300px → 判定为「用户上滚」
    rerender(<Box>上滚后的新内容</Box>);
    await new Promise((r) => setTimeout(r, 20));
    expect(box.scrollTop).toBe(0);                      // 没有被强行拉回底部

    box.scrollTop = SCROLL_HEIGHT - CLIENT_HEIGHT;       // 用户自己滚回底部
    fireEvent.scroll(box);
    rerender(<Box>回底后的新内容</Box>);
    await waitFor(() => expect(box.scrollTop).toBe(SCROLL_HEIGHT));
  });
});
