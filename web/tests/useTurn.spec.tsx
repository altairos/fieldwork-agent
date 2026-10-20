import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useTurn } from "../src/hooks/useTurn";
import { useAuthStore } from "../src/stores/auth";
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

/** 先吐 chunks 再挂起：流式回合中途的真实形态（delta 已到、后续帧未到）。 */
function deltaThenHang(chunks: string[], signal?: AbortSignal) {
  const encoder = new TextEncoder();
  const pending = chunks.map((c) => encoder.encode(c));
  const cancel = vi.fn(async () => {});
  const read = vi.fn(() => {
    if (pending.length) return Promise.resolve({ value: pending.shift() as Uint8Array, done: false });
    return new Promise<never>((_resolve, reject) => {
      if (signal?.aborted) { reject(new Error("Aborted")); return; }
      signal?.addEventListener("abort", () => reject(new Error("Aborted")), { once: true });
    });
  });
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

/** R06a 三路由桩：POST …/messages 走 jsonResp；GET /projects/{pid} 走 snap 响应（记录 URL 次序）；
 *  其余（SSE …/events）走传入桩。 */
function stubApiWithSnapshot(
  post: unknown,
  sse: { resp: unknown },
  snap: { body: unknown; ok?: boolean; status?: number },
) {
  const snapGets: string[] = [];
  const fetchMock = vi.fn(async (url: string) => {
    const u = String(url);
    if (u.endsWith("/messages")) return jsonResp(post, true, 200);
    if (/\/projects\/[^/]+$/.test(u)) {
      snapGets.push(u);
      return jsonResp(snap.body, snap.ok ?? true, snap.status ?? 200);
    }
    return sse.resp;
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, snapGets };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  useConversationStore.getState().reset();
  useToolsStore.getState().reset();
  useProjectStore.getState().reset();
  useUiStore.getState().reset();
  useAuthStore.setState({ user: null, expired: false });
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
    expect(useConversationStore.getState().turnRunning).toBe(false);   // N14：error 帧统一结束 conversation
    expect(useUiStore.getState().degradedBanner?.code).toBe("internal_error");
    expect(useUiStore.getState().degradedBanner?.message).toBe("上游超时");
    expect(result.current.busy).toBe(false);
    expect(result.current.canRetry).toBe(true);       // 失败终态：给重试入口（问题1）
    expect(sse.cancel).toHaveBeenCalledTimes(1);      // error 终止后同样释放连接（Fix #2）
  });

  test("N14：SSE 流在终态前反复截断（重连耗尽）→ onError 复位 conversation（turnRunning=false）", async () => {
    // 回归点：旧实现非终态 EOF 走 onClose（只改 busy），conversation 永远 turnRunning=true。
    // 修复后：流层失败（onError）主动 cancelTurn，与 error 帧终态一致。
    vi.useFakeTimers();
    try {
      const encoder = new TextEncoder();
      const fetchMock = vi.fn(async (url: string) => {
        const u = String(url);
        if (u.endsWith("/messages")) return jsonResp(CREATED, true, 200);
        // /events：每次都吐同一 seq 的 delta 后 EOF（服务端未按 Last-Event-ID 推进）→ 无新进展，重连耗尽
        const queue = [encoder.encode('id: 1\nevent: message_delta\ndata: {"turn_id":"t1","seq":1,"text":"半截"}\n\n')];
        return { ok: true, status: 200, body: { getReader: () => ({
          read: vi.fn(async () => queue.length
            ? { value: queue.shift() as Uint8Array, done: false }
            : { value: undefined, done: true }),
          cancel: vi.fn(async () => {}),
        }) } };
      });
      vi.stubGlobal("fetch", fetchMock);
      const { result } = renderHook(() => useTurn("p1"));
      await act(async () => {
        void result.current.send("帮我排行程");
        await vi.advanceTimersByTimeAsync(60_000);   // 越过 5 次退避（2+4+8+8+8s）直至重连耗尽
      });
      expect(useConversationStore.getState().turnRunning).toBe(false);   // 不再永久脉冲
      expect(useConversationStore.getState().streamBuffer).toBe("");     // 残缺丢弃
      expect(useConversationStore.getState().messages).toEqual([{ role: "user", content: "帮我排行程" }]);
      expect(useUiStore.getState().degradedBanner?.code).toBe("stream_error");
      expect(result.current.busy).toBe(false);
    } finally {
      vi.useRealTimers();
    }
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
    expect(result.current.canRetry).toBe(false);      // 正常完成：不给重试入口（问题1）
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

describe("useTurn — R06a · turn_done 后回读项目快照", () => {
  const SNAP = { project_id: "p1", phase: "informants", research_context: {},
                 field_sites: [], informants: [], proposals: [], phase_history: [] };

  test("首见 turn_done → 恰好一次 GET /projects/p1，快照载入 store，refreshError 保持 null", async () => {
    // 真实前置：进入项目页时快照已先载入 store（D1 守卫依赖 store.projectId 标识当前项目）
    useProjectStore.getState().loadSnapshot(SNAP);
    const sse = sseResp([
      'id: 1\nevent: message_delta\ndata: {"turn_id":"t1","seq":1,"text":"画像完成"}\n\n',
      'id: 2\nevent: turn_done\ndata: {"turn_id":"t1","seq":2,"reply_id":"t1","usage":{"total_tokens":8}}\n\n',
    ]);
    const { snapGets } = stubApiWithSnapshot(CREATED, sse, { body: SNAP });
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.send("调研大理"); await tick(); });

    expect(snapGets).toHaveLength(1);                       // 恰好一次（重复帧不重触发）
    expect(snapGets[0]).toContain("/projects/p1");
    expect(useProjectStore.getState().snapshot).toEqual(SNAP);
    expect(useProjectStore.getState().phase).toBe("informants");
    expect(useProjectStore.getState().refreshError).toBeNull();
  });

  test("回读 500 → refreshError 置位（回合本身不受影响，连接照常释放）", async () => {
    const sse = sseResp([
      'id: 1\nevent: turn_done\ndata: {"turn_id":"t1","seq":1,"reply_id":"t1","usage":{"total_tokens":8}}\n\n',
    ]);
    const { snapGets } = stubApiWithSnapshot(CREATED, sse,
      { body: { detail: "boom" }, ok: false, status: 500 });
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.send("调研大理"); await tick(); });

    expect(snapGets).toHaveLength(1);
    expect(useProjectStore.getState().refreshError).toBe("快照刷新失败，最新田野点/行程可能未显示");
    expect(result.current.busy).toBe(false);               // 回读失败不拖垮回合收尾
    expect(sse.cancel).toHaveBeenCalledTimes(1);
  });

  test("error 帧收尾（无 turn_done）→ 不发起快照回读", async () => {
    const sse = sseResp([
      'id: 1\nevent: error\ndata: {"turn_id":"t1","seq":1,"code":"internal_error","message":"上游超时","hint":"请重试"}\n\n',
    ]);
    const { snapGets } = stubApiWithSnapshot(CREATED, sse, { body: SNAP });
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.send("调研大理"); await tick(); });

    expect(snapGets).toHaveLength(0);
    expect(useProjectStore.getState().refreshError).toBeNull();
  });

  test("竞态：回读期间切换项目后旧快照不写入新 store", async () => {
    const sse = sseResp([
      'id: 1\nevent: turn_done\ndata: {"turn_id":"t1","seq":1,"reply_id":"t1","usage":{"total_tokens":8}}\n\n',
    ]);
    // GET /projects/p1 挂起（捕获 resolve 句柄）：手动控制慢回读的返回时刻，
    // 模拟 await rest.getProject 期间用户已切换项目的竞态窗口
    let resolveSnap!: (resp: unknown) => void;
    const snapPending = new Promise((r) => { resolveSnap = r; });
    const snapGets: string[] = [];
    const fetchMock = vi.fn(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/messages")) return jsonResp(CREATED, true, 200);
      if (/\/projects\/[^/]+$/.test(u)) {
        snapGets.push(u);
        return snapPending;
      }
      return sse.resp;
    });
    const SNAP_A = { project_id: "p1", phase: "informants", research_context: {},
                     field_sites: [], informants: [], proposals: [], phase_history: [] };
    // 真实前置：项目 A 快照已载入 store（D1 守卫依赖 store.projectId 标识当前项目）
    useProjectStore.getState().loadSnapshot(SNAP_A);
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.send("调研大理"); await tick(); });

    expect(snapGets).toHaveLength(1);                    // 回读已发起、仍挂起（竞态窗口内）
    const SNAP_B = { project_id: "p2", phase: "preliminary_fieldwork", research_context: {},
                     field_sites: [], informants: [], proposals: [], phase_history: [] };
    useProjectStore.getState().reset();                  // 用户切换项目：store 先 reset
    useProjectStore.getState().loadSnapshot(SNAP_B);     // 再载入项目 B 的快照
    await act(async () => {                               // 项目 A 的慢回读此刻才返回
      resolveSnap(jsonResp(SNAP_A, true, 200));
      await tick(); await tick();
    });

    expect(useProjectStore.getState().projectId).toBe("p2");   // 旧项目快照被守卫弃置
    expect(useProjectStore.getState().snapshot).toEqual(SNAP_B);
  });
});

describe("useTurn — adopt（领养首页创建的首轮回合，轮 4）", () => {
  /** 独立 fetch 桩：/events → SSE；GET /messages → 后端事实史（adopt 结束后 re-hydrate 用）；
   *  GET /projects/p1 → 项目快照（N10：adopt 终态也回读快照）。 */
  function stubAdopt(sse: { resp: unknown }, history: unknown[], snap: unknown = {}) {
    const snapGets: string[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/events")) return sse.resp;
      if (u === "/projects/p1/messages" && init?.method !== "POST") {
        return jsonResp(history, true, 200);
      }
      if (/\/projects\/[^/]+$/.test(u)) {          // N10：adopt 终态回读项目快照
        snapGets.push(u);
        return jsonResp(snap, true, 200);
      }
      throw new Error(`unexpected ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    return { fetchMock, snapGets };
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

  test("N10：adopt 首见 turn_done → 回读项目快照（修复漏刷致确认按钮为 0）", async () => {
    // 真实前置：进入项目页时快照已先载入 store（守卫依赖 store.projectId 标识当前项目）；
    // 首页首轮画像落盘前 field_sites 为空 → 「确认」按钮为 0
    useProjectStore.getState().loadSnapshot({
      project_id: "p1", phase: "field_site_profiles", research_context: {},
      field_sites: [], informants: [], proposals: [], phase_history: [],
    });
    const sse = sseResp([
      'id: 1\nevent: message_delta\ndata: {"turn_id":"t9","seq":1,"text":"画像完成"}\n\n',
      'id: 2\nevent: turn_done\ndata: {"turn_id":"t9","seq":2,"reply_id":"t9","usage":{}}\n\n',
    ]);
    const savedSnap = {
      project_id: "p1", phase: "field_site_profiles", research_context: {},
      field_sites: [{ id: "site_1", name: "大理", confirmed: false, profile: {} }],
      informants: [], proposals: [], phase_history: [],
    };
    const { snapGets } = stubAdopt(sse, [{ role: "user", content: "研究宗族" }], savedSnap);
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.adopt("t9"); });
    await act(async () => { await tick(); });

    expect(snapGets).toHaveLength(1);                       // adopt 也回读快照（此前漏刷）
    expect(snapGets[0]).toContain("/projects/p1");
    // 轮末落盘的田野点已进 store → 首页首轮即出现「确认」按钮，无需 reload
    expect(useProjectStore.getState().snapshot).toEqual(savedSnap);
  });

  test("N10：adopt 收尾 hydrate 带代次守卫——切换项目后旧响应不写新页 store", async () => {
    // GET /messages 挂起：手动控制慢 hydrate 的返回时刻，模拟 await 期间用户已切换项目
    let resolveMsgs!: (resp: unknown) => void;
    const msgsPending = new Promise((r) => { resolveMsgs = r; });
    const sse = sseResp([
      'id: 1\nevent: turn_done\ndata: {"turn_id":"t9","seq":1,"reply_id":"t9","usage":{}}\n\n',
    ]);
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/events")) return sse.resp;
      if (u === "/projects/p1/messages" && init?.method !== "POST") return msgsPending;
      return jsonResp({}, true, 200);   // /projects/p1 快照
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(({ pid }: { pid: string }) => useTurn(pid),
      { initialProps: { pid: "p1" } });
    await act(async () => { void result.current.adopt("t9"); await tick(); });
    // hydrate 仍挂起时切换项目：cleanup 递增代次 + abort 旧流
    useConversationStore.getState().reset();
    await act(async () => { rerender({ pid: "p2" }); await tick(); });
    // 旧项目 p1 的慢 hydrate 此刻才返回（含 p1 的首问）
    await act(async () => {
      resolveMsgs(jsonResp([{ role: "user", content: "p1 的首问" }], true, 200));
      await tick(); await tick();
    });
    // 代次已变 → 旧响应弃置，不把 p1 的消息写进（已切到 p2 的）store
    expect(useConversationStore.getState().messages).toEqual([]);
  });
});

describe("useTurn — POST 401（App Token 守卫）人话降级（后续小任务 b）", () => {
  test("401 → 人话横幅（不透传后端 JSON）+ onAuthError 回调触发 + 不订阅 SSE", async () => {
    const { fetchMock } = stubApi(
      { code: "app_token_required", message: "缺少或错误的 X-App-Token 请求头" },
      false, 401, { resp: {} });
    const onAuthError = vi.fn();
    const { result } = renderHook(() => useTurn("p1", { onAuthError }));
    await act(async () => { await result.current.send("帮我排行程"); });

    const banner = useUiStore.getState().degradedBanner;
    expect(banner?.code).toBe("send_failed");
    expect(banner?.message).toBe("访问令牌缺失或已失效，发送被拒绝");
    expect(banner?.message).not.toContain("{");            // 不透传后端 JSON 响应体
    expect(banner?.hint).toContain("访问令牌");
    expect(onAuthError).toHaveBeenCalledTimes(1);           // 调用方就地弹密钥层
    expect(result.current.busy).toBe(false);
    expect(useConversationStore.getState().messages).toEqual([]);   // 乐观 UI 已回滚
    expect(useConversationStore.getState().turnRunning).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);             // 第二步 SSE 未发起
  });

  test("401 + 其他 code（非守卫来源）→ 走通用 send_failed（String(err)）不触发回调", async () => {
    stubApi({ code: "upstream_auth" }, false, 401, { resp: {} });
    const onAuthError = vi.fn();
    const { result } = renderHook(() => useTurn("p1", { onAuthError }));
    await act(async () => { await result.current.send("帮我排行程"); });

    expect(onAuthError).not.toHaveBeenCalled();
    expect(useUiStore.getState().degradedBanner?.code).toBe("send_failed");
    expect(result.current.busy).toBe(false);
  });
});

describe("useTurn — cancel 复位 conversation store（P2-9）", () => {
  test("流中取消：turnRunning/streamBuffer 复位、残缺回复不入史、user 消息保留、后端已通知", async () => {
    const cancels: string[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/messages")) return jsonResp(CREATED, true, 200);
      if (u.endsWith("/cancel")) {
        cancels.push(u);
        return jsonResp({ turn_id: "t1", status: "cancelled" }, true, 200);
      }
      // /events：先吐一帧 delta 再挂起（abort 前不再吐帧——SSE 断开收不到终态事件）
      const sig = init?.signal as AbortSignal;
      return {
        ok: true, status: 200,
        body: { getReader: () => deltaThenHang(
          ['id: 1\nevent: message_delta\ndata: {"turn_id":"t1","seq":1,"text":"部分回复"}\n\n'], sig) },
      };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { void result.current.send("帮我排行程"); await tick(); await tick(); });

    // 取消前：流式进行中（delta 已入 buffer、打字光标脉冲）
    expect(useConversationStore.getState().turnRunning).toBe(true);
    expect(useConversationStore.getState().streamBuffer).toBe("部分回复");

    await act(async () => { await result.current.cancel(); await tick(); });

    expect(cancels).toHaveLength(1);                       // 后端已收到取消通知
    expect(result.current.busy).toBe(false);
    const st = useConversationStore.getState();
    expect(st.turnRunning).toBe(false);                    // 不再永久脉冲
    expect(st.streamBuffer).toBe("");                      // 残缺回复丢弃（对齐后端 P1-4）
    expect(st.messages).toEqual([{ role: "user", content: "帮我排行程" }]);  // 已接受的消息不弹掉
  });
});


describe("useTurn — R01 身份作用域：旧身份回合不得写进新身份页面", () => {
  const ALICE = { user_id: "u-alice", email: "alice@example.test" };
  const BOB = { user_id: "u-bob", email: "bob@example.test" };

  /** 可手动放行的 SSE：先吐 initial 帧，之后每次 read 由释放的 chunk 兑现。 */
  function gatedSse(initial: string[]) {
    const encoder = new TextEncoder();
    const queue = initial.map((c) => encoder.encode(c));
    const pending: ((r: { value: Uint8Array; done: boolean }) => void)[] = [];
    const cancel = vi.fn(async () => {});
    const read = vi.fn(() => {
      if (queue.length) return Promise.resolve({ value: queue.shift() as Uint8Array, done: false });
      return new Promise<{ value: Uint8Array; done: boolean }>((resolve) => pending.push(resolve));
    });
    return {
      resp: { ok: true, status: 200, body: { getReader: () => ({ read, cancel }) } },
      release: (chunk: string) => {
        const resolve = pending.shift();
        resolve?.({ value: encoder.encode(chunk), done: false });
      },
      cancel,
    };
  }

  test("首帧后注销并换号：旧流被 abort，晚到的私密帧不写入新身份页面", async () => {
    useAuthStore.setState({ user: ALICE });
    const sse = gatedSse(['id: 1\nevent: message_delta\ndata: {"turn_id":"t1","seq":1,"text":"ALICE_PUBLIC"}\n\n']);
    const signals: AbortSignal[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/messages")) return jsonResp(CREATED, true, 200);
      signals.push(init?.signal as AbortSignal);
      return sse.resp;
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { void result.current.send("Alice 的问题"); await tick(); });
    expect(useConversationStore.getState().streamBuffer).toBe("ALICE_PUBLIC");
    expect(signals).toHaveLength(1);

    // 流未完成时注销并换号（真实链路：useProjectBootstrap 先清四个 store 再重拉）
    await act(async () => {
      useAuthStore.setState({ user: null });
      useConversationStore.getState().reset();
      useAuthStore.setState({ user: BOB });
      await tick();
    });
    expect(signals[0].aborted).toBe(true);          // 旧连接已释放

    // 旧回合的后续帧此刻才到达（真实 SSE 的 abort 与 re-render 之间存在窗口）
    await act(async () => {
      sse.release('id: 2\nevent: message_delta\ndata: {"turn_id":"t1","seq":2,"text":"ALICE_PRIVATE_AFTER_LOGOUT"}\n\n');
      await tick(); await tick();
    });
    const st = useConversationStore.getState();
    expect(st.streamBuffer).toBe("");
    expect(st.messages).toEqual([]);               // 新身份页面无任何旧身份内容
  });

  test("POST 飞行中换号：回合创建成功也不订阅 SSE、不写 store", async () => {
    useAuthStore.setState({ user: ALICE });
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).endsWith("/messages")) { await gate; return jsonResp(CREATED, true, 200); }
      return { ok: true, status: 200, body: { getReader: () => ({ read: vi.fn(async () => ({ value: undefined, done: true })), cancel: vi.fn(async () => {}) }) } };
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => {
      void result.current.send("Alice 的问题");
      await tick();
    });
    // POST 仍挂起时换号
    await act(async () => {
      useAuthStore.setState({ user: null });
      useConversationStore.getState().reset();
      useAuthStore.setState({ user: BOB });
      await tick();
    });
    await act(async () => { release(); await tick(); await tick(); });

    const events = fetchMock.mock.calls.filter(([u]) => String(u).includes("/events"));
    expect(events).toHaveLength(0);                 // 回合代次已死：不订阅
    expect(useConversationStore.getState().messages).toEqual([]);
  });

  test("同身份期间 store 重建同值 user 对象不会误断在飞回合", async () => {
    useAuthStore.setState({ user: ALICE });
    const sse = gatedSse([]);
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/messages")) return jsonResp(CREATED, true, 200);
      signals.push(init?.signal as AbortSignal);
      return sse.resp;
    }));
    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { void result.current.send("继续"); await tick(); });
    // checkSession 会用同值新对象回填 user：身份键不变，不应 abort
    await act(async () => {
      useAuthStore.setState({ user: { ...ALICE } });
      await tick();
    });
    expect(signals[0].aborted).toBe(false);
  });
});

// ------------------------------- 重新生成（Task 11）

describe("useTurn — 重新生成", () => {
  test("retry 成功：POST /turns/t1/retry → 丢末条 assistant → 订阅新回合", async () => {
    useConversationStore.setState({
      messages: [{ role: "user", content: "帮我排行程" },
                 { role: "assistant", content: "残缺回复" }],
      streamBuffer: "", turnRunning: false, lastUsage: {},
    });
    const urls: string[] = [];
    // 已结束且中断的旧回合：turn_done 置 retryable，incomplete 置中断提示
    const sseOne = sseResp([
      'id: 1\nevent: turn_done\n' +
        'data: {"turn_id":"t1","seq":1,"reply_id":"t1","usage":{},"incomplete":true}\n\n',
    ]);
    const sseTwo = sseResp([
      'id: 1\nevent: turn_done\n' +
        'data: {"turn_id":"t2","seq":1,"reply_id":"t2","usage":{},"incomplete":false}\n\n',
    ]);
    const fetchMock = vi.fn(async (url: string) => {
      const u = String(url);
      urls.push(u);
      if (u.endsWith("/retry")) return jsonResp({ turn_id: "t2", status: "running" }, true, 201);
      // adopt 的 re-hydrate：返回当前 store 史（adopt 的 hydrate 是 fire-and-forget，
      // 固定返回 [] 会把断言打空）
      if (u.endsWith("/messages")) return jsonResp(useConversationStore.getState().messages, true, 200);
      if (u.includes("/turns/t2/events")) return sseTwo.resp;
      return sseOne.resp;
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.adopt("t1"); });
    expect(result.current.canRetry).toBe(true);

    await act(async () => { await result.current.retry(); });
    expect(urls.some((u) => u.endsWith("/turns/t1/retry"))).toBe(true);
    expect(urls.some((u) => u.includes("/turns/t2/events"))).toBe(true);
    // 残缺 assistant 已丢弃；新回合无 delta → 仅剩 user 消息
    expect(useConversationStore.getState().messages.map((m) => m.content))
      .toEqual(["帮我排行程"]);
    expect(sseTwo.cancel).toHaveBeenCalled();
    expect(result.current.canRetry).toBe(false); // 新回合正常完成：重试入口关闭（问题1）
  });

  test("retry 被拒（409）→ 如实上屏，不动本地消息", async () => {
    useConversationStore.setState({
      messages: [{ role: "user", content: "问" },
                 { role: "assistant", content: "答" }],
      streamBuffer: "", turnRunning: false, lastUsage: {},
    });
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResp({ detail: "对话末尾不是「用户消息 + 助手回复」" }, false, 409)));

    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.retry("t1"); });
    expect(useConversationStore.getState().messages.map((m) => m.content))
      .toEqual(["问", "答"]);
    expect(useUiStore.getState().degradedBanner?.code).toBe("retry_failed");
  });
});

describe("useTurn — 同步在飞守卫（评审 R4）", () => {
  test("POST 落地前连发两次：只创建一个回合、只 push 一条用户消息", async () => {
    // `busy` 是渲染态：setBusy(true) 要等下一轮渲染才为真，而它原先在 await createTurn
    // 之后才置位——同一 tick 内的第二次 send 看到 busy 仍是 false，会各自 POST 一个回合。
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const sse = sseResp([
      'id: 1\nevent: turn_done\ndata: {"turn_id":"t1","seq":1,"reply_id":"t1","usage":{}}\n\n',
    ]);
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).endsWith("/messages")) { await gate; return jsonResp(CREATED, true, 200); }
      return sse.resp;
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => {
      const first = result.current.send("第一次");
      void result.current.send("第二次");   // 同一 tick，busy 尚未为真
      release();
      await first;
    });

    const posts = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/messages"));
    expect(posts).toHaveLength(1);
    expect(useConversationStore.getState().messages.filter((m) => m.role === "user"))
      .toHaveLength(1);
  });
});
