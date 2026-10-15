import { describe, expect, test, beforeEach, afterEach, vi } from "vitest";
import { parseSseFrames, streamTurnEvents } from "../src/api/sse";
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
  test("多行 data 按 SSE 规范以 \\n 连接后再解析", () => {
    const raw =
      'id: 4\nevent: message_delta\n' +
      'data: {"turn_id":"t1","seq":4,\n' +
      'data: "text":"折行 payload"}\n\n';
    const { frames } = parseSseFrames(raw);
    expect(frames).toHaveLength(1);
    expect(frames[0].data).toEqual({ turn_id: "t1", seq: 4, text: "折行 payload" });
  });
  test("多行 data 不静默串接失真：跨行数字不得被拼成新值", () => {
    // 旧实现直接串接（'…"seq":1' + '2,…'）会把 seq 静默变成 12；
    // 规范连接后得到非法 JSON → 整帧丢弃（宁可丢帧也不失真，与「价格不失真」同源原则）。
    const raw = 'id: 1\nevent: message_delta\ndata: {"turn_id":"t1","seq":1\ndata: 2,"text":"x"}\n\n';
    const { frames } = parseSseFrames(raw);
    expect(frames).toHaveLength(0);
  });
  test("data 只去单个前导空格（SSE 规范），无空格的 data: 同样可解析", () => {
    const withSpace = parseSseFrames('event: message_delta\ndata: {"turn_id":"t","seq":1,"text":"a"}\n\n');
    const noSpace = parseSseFrames('event:message_delta\ndata:{"turn_id":"t","seq":2,"text":"b"}\n\n');
    expect(withSpace.frames[0].data.text).toBe("a");
    expect(noSpace.frames[0].event).toBe("message_delta");
    expect(noSpace.frames[0].data.text).toBe("b");
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

// ══════════════════════════════════════════════════════════════════════════
// streamTurnEvents 连接释放（终审 Fix #2）：后端发完 turn_done 后回到 queue.get()
// 等哨兵（心跳续命），**不主动关流**；客户端若只是停止读取而不 reader.cancel()，
// TCP 连接会滞留 → 浏览器同域 HTTP/1.1 并发上限(~6)被耗尽 → 多回合后前端卡死。
// ══════════════════════════════════════════════════════════════════════════

interface ReaderStub {
  read: ReturnType<typeof vi.fn>;
  cancel: ReturnType<typeof vi.fn>;
}

/** 桩 SSE 响应：chunks 依次吐出（TextEncoder 编码），吐完 done=true。 */
function stubSse(chunks: string[], opts: { failRead?: Error } = {}): ReaderStub {
  const encoder = new TextEncoder();
  const queue = chunks.map((c) => encoder.encode(c));
  const reader: ReaderStub = {
    read: vi.fn(async () => {
      if (opts.failRead) throw opts.failRead;
      return queue.length
        ? { value: queue.shift() as Uint8Array, done: false }
        : { value: undefined, done: true };
    }),
    cancel: vi.fn(async () => {}),
  };
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, status: 200, body: { getReader: () => reader },
  })));
  return reader;
}

describe("streamTurnEvents 终止/出错时释放连接", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  test("收到 turn_done 立即终止并 cancel reader（不再读后续心跳帧）", async () => {
    const reader = stubSse([
      'id: 1\nevent: message_delta\ndata: {"turn_id":"t1","seq":1,"text":"你好"}\n\n',
      'id: 2\nevent: turn_done\ndata: {"turn_id":"t1","seq":2,"reply_id":"t1","usage":{"total_tokens":7}}\n\n',
      ": heartbeat\n\n",                       // turn_done 之后仍有心跳：证明是提前终止而非读完
    ]);
    const seen: EventEnvelope[] = [];
    let closed = 0;
    await streamTurnEvents("p1", "t1", {
      onEvent: (f) => seen.push(f),
      onClose: () => { closed += 1; },
    });
    expect(seen.map((f) => f.event)).toEqual(["message_delta", "turn_done"]);
    expect(reader.read).toHaveBeenCalledTimes(2);   // 未继续读第三帧
    expect(reader.cancel).toHaveBeenCalledTimes(1); // 连接被释放
    expect(closed).toBe(1);
  });

  test("流自然结束(done)也 cancel reader 并回调 onClose", async () => {
    const reader = stubSse([]);
    let closed = 0;
    await streamTurnEvents("p1", "t1", { onEvent: () => {}, onClose: () => { closed += 1; } });
    expect(reader.cancel).toHaveBeenCalledTimes(1);
    expect(closed).toBe(1);
  });

  test("read 抛出时 finally 仍 cancel reader（重连耗尽后如实 onError）", async () => {
    const reader = stubSse([], { failRead: new Error("socket hang up") });
    const errors: Error[] = [];
    await streamTurnEvents("p1", "t1", {
      onEvent: () => {},
      onError: (e) => errors.push(e),
      maxReconnects: 0,                       // 不重连，直接走 onError 分支
    });
    expect(reader.cancel).toHaveBeenCalledTimes(1);
    expect(errors.map((e) => e.message)).toEqual(["socket hang up"]);
  });
});
