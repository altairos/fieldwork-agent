import { Profiler } from "react";
import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Timeline } from "../src/components/chat/Timeline";
import { useConversationStore } from "../src/stores/conversation";
import { useProjectStore } from "../src/stores/project";
import { useToolsStore } from "../src/stores/tools";

/**
 * 流式渲染性能不变量（评审 P0-2 的回归护栏）。
 *
 * 背景：store 每收一个 message_delta 就追加 streamBuffer。若 Timeline 按值订阅它，
 * 整条时间线每 token 重渲染一次，历史消息随之重解析 markdown，成本随会话变长叠加。
 *
 * 度量方法：AssistantStream 必须打桩——它是每 delta 都该更新的组件，若用真组件，
 * Profiler 会把它的 commit 算进来（实测 21 次，量与时间线无关），度量对象就错了。
 * 打桩后 Timeline 子树里只剩"父级重渲染才会动"的部分，commit 数即时间线自身重渲染次数。
 */
const stubs = vi.hoisted(() => ({ mdRenders: [] as string[], streamRenders: 0 }));

vi.mock("../src/components/chat/AssistantStream", () => ({
  // 历史消息的 markdown 走这里：用于断言"是否被重新解析"
  MarkdownBody: ({ text }: { text: string }) => {
    stubs.mdRenders.push(text);
    return <div>{text}</div>;
  },
  // 桩：不订阅 store，因此任何一次渲染都只能来自父级 Timeline
  AssistantStream: () => { stubs.streamRenders += 1; return null; },
}));

/** 15 条历史 + 20 个 delta：历史长度足以让"每 delta 全量重解析"变成可见开销。 */
const HISTORY = Array.from({ length: 15 }, (_, i) => ({
  role: "assistant" as const,
  content: `历史消息${i}`,
}));

beforeEach(() => {
  useConversationStore.setState({
    messages: HISTORY, streamBuffer: "", turnRunning: true,
    hydrated: true, lastUsage: {}, quickReplyGroups: [],
  });
  useProjectStore.getState().reset();
  useToolsStore.getState().reset();
  stubs.mdRenders.length = 0;
  stubs.streamRenders = 0;
});

function pushDeltas(n: number) {
  for (let i = 0; i < n; i++) {
    act(() => useConversationStore.getState().applyEvent({
      event: "message_delta", text: `片${i}`,
    } as never));
  }
}

describe("流式渲染不引起时间线重渲染", () => {
  it("20 个 message_delta 最多触发 1 次 Timeline commit（空态布尔首次翻转）", () => {
    const commits: string[] = [];
    render(
      <Profiler id="timeline" onRender={(_id, phase) => { commits.push(phase); }}>
        <Timeline />
      </Profiler>,
    );
    commits.length = 0;                       // 丢弃挂载期 commit
    pushDeltas(20);
    // 若 Timeline 直接订阅 streamBuffer，这里会是 20 次
    expect(commits.length).toBeLessThanOrEqual(1);
  });

  it("20 个 delta 后历史消息的 markdown 不重新解析（memo 生效）", () => {
    render(<Timeline />);
    const before = stubs.mdRenders.filter((t) => t.startsWith("历史消息")).length;
    expect(before).toBe(15);                  // 挂载时每条解析一次
    pushDeltas(20);
    const after = stubs.mdRenders.filter((t) => t.startsWith("历史消息")).length;
    expect(after).toBe(before);
  });
});
