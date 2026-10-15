import { beforeEach, describe, expect, test } from "vitest";
import type { SseEvent } from "../src/types/events";
import { useToolsStore } from "../src/stores/tools";
import { useConversationStore } from "../src/stores/conversation";
import { useProjectStore } from "../src/stores/project";
import { useUiStore } from "../src/stores/ui";

const ev = (e: SseEvent) => e;

beforeEach(() => {
  useToolsStore.getState().reset();
  useConversationStore.getState().reset();
  useProjectStore.getState().reset();
  useUiStore.getState().reset();
});

describe("tools store 幂等", () => {
  test("重复 tool_completed 同 call_id 只留一条(覆盖非追加)", () => {
    const s = useToolsStore.getState();
    s.applyEvent(ev({ event: "tool_started", call_id: "c1", tool: "search_hotels", args: { dest_name: "大理" }, purpose: "住宿" }));
    s.applyEvent(ev({ event: "tool_completed", call_id: "c1", ok: true, data: { items: [{ name: "A", price: 180 }] } }));
    s.applyEvent(ev({ event: "tool_completed", call_id: "c1", ok: true, data: { items: [{ name: "A", price: 180 }] } })); // 重复(重连补发)
    const after = useToolsStore.getState();
    expect(Object.keys(after.byCallId)).toEqual(["c1"]);
    expect(after.byCallId["c1"].status).toBe("done");
    expect(after.byCallId["c1"].tool).toBe("search_hotels");
  });
  test("tool_started→running, tool_completed(ok=false)→failed", () => {
    const s = useToolsStore.getState();
    s.applyEvent(ev({ event: "tool_started", call_id: "c2", tool: "search_flights", args: {}, purpose: "" }));
    expect(useToolsStore.getState().byCallId["c2"].status).toBe("running");
    s.applyEvent(ev({ event: "tool_completed", call_id: "c2", ok: false, data: null, degraded: "flyai_unavailable" }));
    const t = useToolsStore.getState().byCallId["c2"];
    expect(t.status).toBe("failed");
    expect(t.degraded).toBe("flyai_unavailable");
  });
});

describe("conversation store 流式拼接", () => {
  test("message_delta×3 拼接 + turn_done 定格", () => {
    const s = useConversationStore.getState();
    s.applyEvent(ev({ event: "message_delta", text: "正在" }));
    s.applyEvent(ev({ event: "message_delta", text: "搜索" }));
    s.applyEvent(ev({ event: "message_delta", text: "大理" }));
    expect(useConversationStore.getState().streamBuffer).toBe("正在搜索大理");
    expect(useConversationStore.getState().turnRunning).toBe(true);
    s.applyEvent(ev({ event: "turn_done", reply_id: "t1", usage: { total_tokens: 42 } }));
    const after = useConversationStore.getState();
    expect(after.turnRunning).toBe(false);
    expect(after.messages.at(-1)?.content).toBe("正在搜索大理");
    expect(after.messages.at(-1)?.role).toBe("assistant");
    expect(after.lastUsage).toEqual({ total_tokens: 42 });
  });
});

describe("project + ui 阶段推进(右栏不自动切)", () => {
  test("phase_transition 改 phase，ui.activeTab 不变，pendingSuggestion 置位", () => {
    useUiStore.getState().setActiveTab("progress");
    useProjectStore.getState().applyEvent(ev({ event: "phase_transition", from: "research_positioning", to: "field_site_profiles" }));
    useUiStore.getState().applyEvent(ev({ event: "phase_transition", from: "research_positioning", to: "field_site_profiles" }));
    expect(useProjectStore.getState().phase).toBe("field_site_profiles");
    expect(useUiStore.getState().activeTab).toBe("progress");           // 不自动切
    expect(useUiStore.getState().pendingSuggestion).not.toBeNull();     // 只给建议
  });
  test("error 事件置 degradedBanner，不中断已有内容", () => {
    useUiStore.getState().applyEvent(ev({ event: "error", code: "internal_error", message: "boom", hint: "重试" }));
    expect(useUiStore.getState().degradedBanner?.code).toBe("internal_error");
  });
});

describe("全序列集成", () => {
  test("[tool_started,tool_completed,tool_completed重复,delta×3,turn_done] 后各 store 一致", () => {
    const seq: SseEvent[] = [
      { event: "tool_started", call_id: "c1", tool: "search_hotels", args: {}, purpose: "" },
      { event: "tool_completed", call_id: "c1", ok: true, data: { items: [] } },
      { event: "tool_completed", call_id: "c1", ok: true, data: { items: [] } },
      { event: "message_delta", text: "a" },
      { event: "message_delta", text: "b" },
      { event: "message_delta", text: "c" },
      { event: "turn_done", reply_id: "t1", usage: {} },
    ];
    for (const e of seq) {
      useToolsStore.getState().applyEvent(e);
      useConversationStore.getState().applyEvent(e);
      useProjectStore.getState().applyEvent(e);
      useUiStore.getState().applyEvent(e);
    }
    expect(Object.keys(useToolsStore.getState().byCallId)).toEqual(["c1"]);
    expect(useConversationStore.getState().messages.at(-1)?.content).toBe("abc");
    expect(useConversationStore.getState().turnRunning).toBe(false);
  });
});
