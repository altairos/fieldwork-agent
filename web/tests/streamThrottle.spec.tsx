import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantStream } from "../src/components/chat/AssistantStream";
import { useConversationStore } from "../src/stores/conversation";

/**
 * 流式渲染节流的回归护栏（评审 P0-2 第二半）。
 *
 * store 的 streamBuffer 每 delta 追加（不丢字），渲染层按 FLUSH_MS 降频解析 markdown。
 * 度量点选"markdown 解析次数"而不是"渲染次数"：真实成本在 remarkGfm 解析上，
 * 且这个量可以直接和 delta 数对比（节流生效时应远小于 delta 数）。
 */
const mdRenders = vi.hoisted(() => [] as string[]);

vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => {
    mdRenders.push(children);
    return <div>{children}</div>;
  },
}));

beforeEach(() => {
  useConversationStore.setState({
    messages: [], streamBuffer: "", turnRunning: true,
    hydrated: true, lastUsage: {}, quickReplyGroups: [],
  });
  mdRenders.length = 0;
});

describe("流式正文渲染节流", () => {
  it("20 个连续 delta 的 markdown 解析次数 ≤ 2（首帧即时 + 排程窗口内不追加）", () => {
    render(<AssistantStream />);
    mdRenders.length = 0;                     // 丢弃空态首渲染
    for (let i = 0; i < 20; i++) {
      act(() => useConversationStore.getState().applyEvent({
        event: "message_delta", text: `片${i}`,
      } as never));
    }
    // 无节流时这里等于 20
    expect(mdRenders.length).toBeLessThanOrEqual(2);
    expect(mdRenders[0]).toContain("片0");    // 首帧同步上屏，不制造"开始无反应"的观感
  });

  it("清空（turn_done/cancel）立即生效：不留延迟的残留正文", () => {
    render(<AssistantStream />);
    act(() => useConversationStore.getState().applyEvent({
      event: "message_delta", text: "半句",
    } as never));
    act(() => useConversationStore.setState({ streamBuffer: "", turnRunning: false }));
    const { container } = render(<AssistantStream />);
    expect(container.firstChild).toBeNull();
  });
});
