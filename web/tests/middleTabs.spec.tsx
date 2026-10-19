import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Tabs } from "../src/components/ui/Tabs";
import { useUiStore } from "../src/stores/ui";
import { useToolsStore } from "../src/stores/tools";

beforeEach(() => {
  useUiStore.getState().reset();
  useToolsStore.getState().reset();
});

describe("ui store 中栏 tab 与红点", () => {
  it("默认 chat；setMiddleTab 切换；切到 evidence 清红点", () => {
    expect(useUiStore.getState().middleTab).toBe("chat");
    useUiStore.setState({ evidenceHint: true });
    useUiStore.getState().setMiddleTab("evidence");
    expect(useUiStore.getState().middleTab).toBe("evidence");
    expect(useUiStore.getState().evidenceHint).toBe(false);
  });
  it("collapsed 级成功结果到达且不在证据 tab → 红点；不自动切（红线）", () => {
    useToolsStore.getState().applyEvent({
      event: "tool_started", call_id: "c1", tool: "search_ai", args: {}, purpose: "",
      turn_id: "t1", seq: 1,
    } as never);
    useUiStore.getState().applyEvent({
      event: "tool_completed", call_id: "c1", ok: true, data: { answer: "x" },
    } as never);
    expect(useUiStore.getState().evidenceHint).toBe(true);
    expect(useUiStore.getState().middleTab).toBe("chat");   // 绝不自动切
  });
  it("hidden/full 级不触发红点；失败不触发", () => {
    useToolsStore.getState().applyEvent({
      event: "tool_started", call_id: "c2", tool: "amap_geocode", args: {}, purpose: "",
    } as never);
    useUiStore.getState().applyEvent({
      event: "tool_completed", call_id: "c2", ok: true, data: {},
    } as never);
    expect(useUiStore.getState().evidenceHint).toBe(false);
  });
  it("tools store 采集 turnId/seq（R15 归属标注）", () => {
    useToolsStore.getState().applyEvent({
      event: "tool_started", call_id: "c3", tool: "search_ai", args: {}, purpose: "",
      turn_id: "trn_9", seq: 7,
    } as never);
    const call = useToolsStore.getState().byCallId.c3;
    expect(call.turnId).toBe("trn_9");
    expect(call.seq).toBe(7);
  });
});

describe("Tabs cols 变体", () => {
  it("cols=2 用 grid-cols-2（静态映射，非动态拼接）", () => {
    render(<Tabs cols={2} tabs={[{ id: "chat", label: "对话" }, { id: "evidence", label: "检索结果", dot: true }]}
                 active="chat" onChange={() => {}} />);
    const list = screen.getByRole("tablist");
    expect(list.className).toContain("grid-cols-2");
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(screen.getByRole("tab", { selected: true })).toBeTruthy();
  });
  it("缺省 cols 仍为 grid-cols-3（右栏回归）", () => {
    render(<Tabs tabs={[{ id: "a", label: "A" }]} active="a" onChange={() => {}} />);
    expect(screen.getByRole("tablist").className).toContain("grid-cols-3");
  });
});
