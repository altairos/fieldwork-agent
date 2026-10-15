import { describe, expect, test, beforeEach } from "vitest";
import { parseSseFrames } from "../src/api/sse";
import { dispatchEvent } from "../src/lib/dispatch";
import { useToolsStore } from "../src/stores/tools";
import { useConversationStore } from "../src/stores/conversation";
import type { EventEnvelope } from "../src/types/events";

describe("parseSseFrames", () => {
  test("解析单帧(id/event/data)", () => {
    const raw = 'id: 3\nevent: message_delta\ndata: {"turn_id":"t1","seq":3,"text":"你好"}\n\n';
    const { frames, rest } = parseSseFrames(raw);
    expect(frames).toHaveLength(1);
    expect(frames[0].seq).toBe(3);
    expect(frames[0].event).toBe("message_delta");
    expect(frames[0].data).toEqual({ turn_id: "t1", seq: 3, text: "你好" });
    expect(rest).toBe("");
  });
  test("半帧留在 rest(粘包/分包)", () => {
    const raw = 'id: 1\nevent: message_delta\ndata: {"turn_id":"t1","seq":1,"te';
    const { frames, rest } = parseSseFrames(raw);
    expect(frames).toHaveLength(0);
    expect(rest).toBe(raw);
  });
  test("心跳注释帧被忽略", () => {
    const raw = ': heartbeat\n\nid: 2\nevent: turn_done\ndata: {"turn_id":"t1","seq":2,"reply_id":"t1","usage":{}}\n\n';
    const { frames } = parseSseFrames(raw);
    expect(frames).toHaveLength(1);
    expect(frames[0].event).toBe("turn_done");
  });
  test("多帧一次解析", () => {
    const raw =
      'id: 1\nevent: message_delta\ndata: {"turn_id":"t","seq":1,"text":"a"}\n\n' +
      'id: 2\nevent: message_delta\ndata: {"turn_id":"t","seq":2,"text":"b"}\n\n';
    const { frames, rest } = parseSseFrames(raw);
    expect(frames).toHaveLength(2);
    expect(rest).toBe("");
  });
});

describe("dispatchEvent 去重", () => {
  beforeEach(() => {
    useToolsStore.getState().reset();
    useConversationStore.getState().reset();
  });
  test("按 seq 去重：重复 seq 只处理一次", () => {
    const mk = (seq: number): EventEnvelope => ({
      turn_id: "t", seq, event: "message_delta", data: { turn_id: "t", seq, text: "x" },
    });
    const seen1 = dispatchEvent(mk(1), new Set());
    const seen2 = dispatchEvent(mk(1), seen1);   // 重复 seq
    expect(seen2).toEqual(seen1);                // 集合不变
    // conversation 只收到一次 delta
    expect(useConversationStore.getState().streamBuffer).toBe("x");
  });
  test("envelope.data 平铺字段被正确还原为 SseEvent 分派", () => {
    let seen = new Set<number>();
    seen = dispatchEvent({ turn_id: "t", seq: 5, event: "tool_started",
      data: { turn_id: "t", seq: 5, call_id: "c1", tool: "search_hotels", args: {}, purpose: "" } }, seen);
    expect(useToolsStore.getState().byCallId["c1"].status).toBe("running");
  });
});
