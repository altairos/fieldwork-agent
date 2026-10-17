import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useTurn } from "../src/hooks/useTurn";
import { useConversationStore } from "../src/stores/conversation";
import { useProjectStore } from "../src/stores/project";
import { useToolsStore } from "../src/stores/tools";
import { useUiStore } from "../src/stores/ui";

/**
 * useTurn 回合句柄（R4 两步式）行为测试 — 终审 Fix #1/#3：
 * 1. POST /messages 被拒（409 / 5xx）→ failTurn 回滚乐观 UI：被拒消息移除 + turnRunning=false，
 *    且**不**发起第二步 SSE 订阅；
 * 2. SSE 阶段异常只如实上屏，**不**回滚已被后端接受的用户消息（failTurn 调用点的精确边界）；
 * 3. 组件卸载 / projectId 变化 → abort 进行中的流（防旧项目事件污染新项目 store + 连接泄漏）；
 * 4. 流正常终止（turn_done）后 reader 被取消（连接释放，见 sse.spec.ts 的单元覆盖）。
 */

/** rest.req 需要的最小 Response 形状（ok/status/json/text）。 */
function jsonResp(payload: unknown, ok: boolean, status: number) {
  return { ok, status, json: async () => payload, text: async () => JSON.stringify(payload) };
}

const CREATED = { turn_id: "t1", status: "running" };

/** SSE 响应桩：chunks 依次吐出，吐完 done=true；返回 read/cancel 以便断言连接释放。 */
function sseResp(chunks: string[]) {
  const encoder = new TextEncoder();
  const queue = chunks.map((c) => encoder.encode(c));
  const cancel = vi.fn(async () => {});
  const read = vi.fn(async () => (queue.length
    ? { value: queue.shift() as Uint8Array, done: false }
    : { value: undefined, done: true }));
  return { resp: { ok: true, status: 200, body: { getReader: () => ({ read, cancel }) } }, read, cancel };
}

/** 挂起读：直到 signal abort 才结束（模拟后端发完帧后靠心跳续命、不主动关流）。 */
function hangingReader(signal?: AbortSignal) {
  const cancel = vi.fn(async () => {});
  const read = vi.fn(() => new Promise<never>((_resolve, reject) => {
    if (signal?.aborted) { reject(new Error("Aborted")); return; }
    signal?.addEventListener("abort", () => reject(new Error("Aborted")), { once: true });
  }));
  return { read, cancel };
}

/** 双路由桩：POST …/messages 走 jsonResp，GET …/events 走 SSE 桩；SSE 的 signal 收集到 signals。 */
function stubApi(post: unknown, ok: boolean, status: number, sse: { resp: unknown } | "hang") {
  const signals: AbortSignal[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).endsWith("/messages")) return jsonResp(post, ok, status);
    signals.push(init?.signal as AbortSignal);
    if (sse === "hang") return { ok: true, status: 200, body: { getReader: () => hangingReader(signals.at(-1)) } };
    return sse.resp;
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, signals };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  useConversationStore.getState().reset();
  useToolsStore.getState().reset();
  useProjectStore.getState().reset();
  useUiStore.getState().reset();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useTurn — POST 被拒时回滚乐观 UI（Fix #3）", () => {
  test("409（已有回合在执行）→ conflict 置位、被拒消息移除、turnRunning=false、不订阅 SSE", async () => {
    const { fetchMock } = stubApi({ detail: "已有回合在执行" }, false, 409, { resp: {} });
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.send("帮我排行程"); });

    expect(result.current.conflict).toBe(true);       // 如实提示冲突，不重试
    expect(result.current.busy).toBe(false);
    const st = useConversationStore.getState();
    expect(st.messages).toEqual([]);                  // 被拒消息不留痕
    expect(st.turnRunning).toBe(false);               // 打字光标不再永久脉冲
    expect(st.streamBuffer).toBe("");
    expect(fetchMock).toHaveBeenCalledTimes(1);       // R4 第二步未发起
    expect(useUiStore.getState().degradedBanner).toBeNull();  // 409 走 conflict 而非降级横幅
  });

  test("5xx → send_failed 如实上屏（不静默吞错）+ 同样回滚", async () => {
    const { fetchMock } = stubApi({ detail: "boom" }, false, 500, { resp: {} });
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.send("帮我排行程"); });

    expect(result.current.conflict).toBe(false);
    expect(useUiStore.getState().degradedBanner?.code).toBe("send_failed");
    expect(useConversationStore.getState().messages).toEqual([]);
    expect(useConversationStore.getState().turnRunning).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("回滚只弹本轮：历史对话原样保留", async () => {
    useConversationStore.setState({
      messages: [{ role: "user", content: "上一轮" }, { role: "assistant", content: "上一轮回复" }],
      streamBuffer: "", turnRunning: false, lastUsage: {},
    });
    stubApi({ detail: "conflict" }, false, 409, { resp: {} });
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.send("这一轮"); });
    expect(useConversationStore.getState().messages.map((m) => m.content)).toEqual(["上一轮", "上一轮回复"]);
  });
});

describe("useTurn — SSE 阶段（消息已被接受，绝不回滚）", () => {
  test("流内 error 事件如实上屏，用户消息保留", async () => {
    const sse = sseResp([
      'id: 1\nevent: error\ndata: {"turn_id":"t1","seq":1,"code":"internal_error","message":"上游超时","hint":"请重试"}\n\n',
    ]);
    stubApi(CREATED, true, 200, sse);
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.send("帮我排行程"); });

    expect(useConversationStore.getState().messages).toEqual([{ role: "user", content: "帮我排行程" }]);
    expect(useUiStore.getState().degradedBanner?.code).toBe("internal_error");
    expect(useUiStore.getState().degradedBanner?.message).toBe("上游超时");
    expect(result.current.busy).toBe(false);
    expect(sse.cancel).toHaveBeenCalledTimes(1);      // error 终止后同样释放连接（Fix #2）
  });

  test("正常回合：delta 拼接 + turn_done 定格，busy 复位且连接释放", async () => {
    const sse = sseResp([
      'id: 1\nevent: message_delta\ndata: {"turn_id":"t1","seq":1,"text":"正在"}\n\n',
      'id: 2\nevent: message_delta\ndata: {"turn_id":"t1","seq":2,"text":"核对报价"}\n\n',
      'id: 3\nevent: turn_done\ndata: {"turn_id":"t1","seq":3,"reply_id":"t1","usage":{"total_tokens":12}}\n\n',
      ": heartbeat\n\n",
    ]);
    stubApi(CREATED, true, 200, sse);
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.send("帮我排行程"); });

    expect(useConversationStore.getState().messages).toEqual([
      { role: "user", content: "帮我排行程" },
      { role: "assistant", content: "正在核对报价" },
    ]);
    expect(useConversationStore.getState().turnRunning).toBe(false);
    expect(useConversationStore.getState().lastUsage).toEqual({ total_tokens: 12 });
    expect(result.current.busy).toBe(false);
    expect(sse.cancel).toHaveBeenCalledTimes(1);
    expect(useUiStore.getState().degradedBanner).toBeNull();
  });
});

describe("useTurn — 卸载/跨项目切换中止旧流（Fix #1）", () => {
  test("组件卸载 → 进行中的 SSE 流被 abort（不再写 store、不留连接）", async () => {
    const { signals } = stubApi(CREATED, true, 200, "hang");
    const { result, unmount } = renderHook(() => useTurn("p1"));
    await act(async () => { void result.current.send("开始田野工作"); await tick(); });

    expect(signals).toHaveLength(1);
    expect(signals[0].aborted).toBe(false);
    unmount();
    expect(signals[0].aborted).toBe(true);
    await act(async () => { await tick(); });         // abort 引发的流收尾在测试内落定
  });

  test("projectId 变化 → 旧项目的流被 abort（跨项目不串台）", async () => {
    const { signals } = stubApi(CREATED, true, 200, "hang");
    const { result, rerender } = renderHook(({ pid }: { pid: string }) => useTurn(pid), {
      initialProps: { pid: "pA" },
    });
    await act(async () => { void result.current.send("A 项目回合"); await tick(); });

    expect(signals).toHaveLength(1);
    expect(signals[0].aborted).toBe(false);
    // cleanup 先 abort 旧流再用新 projectId 起 effect；abort 收尾的 setBusy 也须落在 act 内
    await act(async () => { rerender({ pid: "pB" }); await tick(); });
    expect(signals[0].aborted).toBe(true);            // 旧流已断：其回调不会再污染新项目 store
  });
});

describe("useTurn — adopt（领养首页创建的首轮回合，轮 4）", () => {
  /** 独立 fetch 桩：/events → SSE；GET /messages → 后端事实史（adopt 结束后 re-hydrate 用）。 */
  function stubAdopt(sse: { resp: unknown }, history: unknown[]) {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/events")) return sse.resp;
      if (String(url) === "/projects/p1/messages" && init?.method !== "POST") {
        return jsonResp(history, true, 200);
      }
      throw new Error(`unexpected ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    return { fetchMock };
  }

  test("不 POST 创建回合；回放事件进 store；结束后 re-hydrate 对齐后端事实史（含首问）", async () => {
    const sse = sseResp([
      'id: 1\nevent: message_delta\ndata: {"turn_id":"t9","seq":1,"text":"你好，"}\n\n',
      'id: 2\nevent: message_delta\ndata: {"turn_id":"t9","seq":2,"text":"田野罗盘"}\n\n',
      'id: 3\nevent: turn_done\ndata: {"turn_id":"t9","seq":3,"reply_id":"t9","usage":{}}\n\n',
    ]);
    const { fetchMock } = stubAdopt(sse, [
      { role: "user", content: "研究宗族" },
      { role: "assistant", content: "你好，田野罗盘" },
    ]);
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.adopt("t9"); });
    await act(async () => { await tick(); });   // re-hydrate 的异步收敛落定

    // 领养 ≠ 创建：绝无 POST /messages
    const posts = fetchMock.mock.calls.filter(
      ([u, i]: [string, RequestInit?]) => String(u).endsWith("/messages") && i?.method === "POST");
    expect(posts).toHaveLength(0);
    // 回放内容已进 store，且 re-hydrate 后最终一致（user 首问竞态由覆盖式 hydrate 修正）
    expect(useConversationStore.getState().messages).toEqual([
      { role: "user", content: "研究宗族" },
      { role: "assistant", content: "你好，田野罗盘" },
    ]);
    expect(useConversationStore.getState().turnRunning).toBe(false);
    expect(result.current.busy).toBe(false);
    expect(sse.cancel).toHaveBeenCalledTimes(1);      // turn_done 后连接释放
  });

  test("重复领养先断旧流（StrictMode 双执行/防双订阅泄漏）", async () => {
    const signals: AbortSignal[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/events")) {
        const sig = init?.signal as AbortSignal;
        signals.push(sig);
        return { ok: true, status: 200, body: { getReader: () => hangingReader(sig) } };
      }
      return jsonResp([], true, 200);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { void result.current.adopt("t1"); await tick(); });
    await act(async () => { void result.current.adopt("t2"); await tick(); });

    expect(signals).toHaveLength(2);                  // 两次订阅各起一流
    expect(signals[0].aborted).toBe(true);           // 第二次领养先断第一次的流
    expect(signals[1].aborted).toBe(false);
  });
});
