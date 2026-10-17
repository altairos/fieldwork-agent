import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { request as httpRequest, type IncomingHttpHeaders, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import type { CreateTurnResponse, EventEnvelope, SseEvent } from "../src/types/events";
import type { BudgetBreakdown, ItemPage, ProjectSnapshot } from "../src/types/models";
import { startMockServer } from "../mock/sseServer";

// ---------------------------------------------------------------- 类型编译校验

describe("types compile & shape", () => {
  test("event envelope shape", () => {
    const env: EventEnvelope = { turn_id: "t1", seq: 1, event: "message_delta", data: { text: "hi" } };
    expect(env.seq).toBe(1);
  });
  test("tool_completed degraded optional", () => {
    const ev: SseEvent = { event: "tool_completed", call_id: "c1", ok: false, data: null, degraded: "flyai_unavailable" };
    expect(ev.event).toBe("tool_completed");
  });
  test("tool_completed error optional", () => {
    const ev: SseEvent = { event: "tool_completed", call_id: "c1", ok: false, data: null,
      error: { code: "flyai_rate_limited", message: "rate limited", hint: "申请 https://flyai.open.fliggy.com/" } };
    expect(ev.event).toBe("tool_completed");
  });
  test("item page price nullable", () => {
    const page: ItemPage = { items: [{ name: "x", price: null, price_raw: null, jump_url: null, pic_url: null, extra: {} }], total: 1, cheapest: null, dearest: null };
    expect(page.items[0].price).toBeNull();
  });
  test("budget coverage field", () => {
    const b: BudgetBreakdown = { items: [], total: 0, budget_total: 20000, over_budget: false, coverage: "partial", uncovered: ["住宿"] };
    expect(b.coverage).toBe("partial");
  });
  test("create turn response shape", () => {
    const r: CreateTurnResponse = { turn_id: "t1", status: "running" };
    expect(r.status).toBe("running");
  });
  test("project snapshot partial context", () => {
    const s: ProjectSnapshot = {
      project_id: "p1", phase: "research_positioning",
      research_context: { discipline: "社会学" },
      field_sites: [], informants: [], proposals: [], phase_history: [],
    };
    expect(s.research_context.topic).toBeUndefined();
  });
});

// ---------------------------------------------------------------- mock server 冒烟

interface HttpResult {
  status: number;
  headers: IncomingHttpHeaders;
  body: string;
}

/** 最小 node:http 客户端（SSE 流由服务端脚本发完后主动关闭，故收完即 resolve）。 */
function call(port: number, method: string, path: string,
              opts: { headers?: Record<string, string>; body?: string } = {}): Promise<HttpResult> {
  return new Promise((resolve, reject) => {
    const req = httpRequest({ host: "127.0.0.1", port, method, path, headers: opts.headers }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk: string) => (body += chunk));
      res.on("end", () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body }));
    });
    req.on("error", reject);
    if (opts.body !== undefined) req.write(opts.body);
    req.end();
  });
}

interface ParsedFrame {
  id: number;
  event: string;
  data: Record<string, unknown>;
}

function parseFrames(raw: string): ParsedFrame[] {
  return raw.split("\n\n").filter(Boolean).map((block) => {
    const lines = block.split("\n");
    const idLine = lines.find((l) => l.startsWith("id: "));
    const eventLine = lines.find((l) => l.startsWith("event: "));
    const dataLine = lines.find((l) => l.startsWith("data: "));
    expect(idLine, `frame missing id line: ${block}`).toBeDefined();
    expect(eventLine, `frame missing event line: ${block}`).toBeDefined();
    expect(dataLine, `frame missing data line: ${block}`).toBeDefined();
    return {
      id: Number(idLine!.slice(4)),
      event: eventLine!.slice(7),
      data: JSON.parse(dataLine!.slice(6)) as Record<string, unknown>,
    };
  });
}

/** 演示回合的 11 帧事件名序列（对齐 brief Step 3 脚本）。 */
const EXPECTED_SEQUENCE = [
  "message_delta", "message_delta", "message_delta",
  "tool_started", "tool_completed",
  "tool_started", "tool_completed",
  "message_delta", "message_delta",
  "phase_transition", "turn_done",
];

describe("mock sse server smoke", () => {
  let server: Server;
  let port: number;

  beforeAll(async () => {
    server = await startMockServer(0, 5); // 临时端口 + 5ms 帧间隔（测试快速跑完）
    port = (server.address() as AddressInfo).port;
  });

  afterAll(async () => {
    await new Promise<void>((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  });

  test("two-step flow: POST /messages then GET events replays full turn", async () => {
    const created = await call(port, "POST", "/projects/p1/messages", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "帮我搜索大理住宿" }),
    });
    expect(created.status).toBe(201);
    const turn = JSON.parse(created.body) as CreateTurnResponse;
    expect(turn.turn_id).toMatch(/^turn_/);
    expect(turn.status).toBe("running");

    const stream = await call(port, "GET", `/projects/p1/turns/${turn.turn_id}/events`);
    expect(stream.status).toBe(200);
    expect(stream.headers["content-type"]).toBe("text/event-stream");

    // 帧格式与 sse.py encode_event 完全一致：id:/event:/data: 三行 + 空行分隔
    expect(stream.body.endsWith("\n\n")).toBe(true);
    for (const block of stream.body.split("\n\n").filter(Boolean)) {
      expect(block).toMatch(/^id: \d+\nevent: [a-z_]+\ndata: \{.*\}$/s);
    }

    const frames = parseFrames(stream.body);
    expect(frames).toHaveLength(11);
    expect(frames.map((f) => f.event)).toEqual(EXPECTED_SEQUENCE);
    // seq 从 1 单调递增；data 内 turn_id/seq 平铺且与 id 行一致
    frames.forEach((f, i) => {
      expect(f.id).toBe(i + 1);
      expect(f.data.seq).toBe(i + 1);
      expect(f.data.turn_id).toBe(turn.turn_id);
    });
    // 关键帧内容抽查
    expect(frames[0].data.text).toBe("正在");
    expect(frames[3].data).toMatchObject({ call_id: "c1", tool: "search_hotels", args: { dest_name: "大理" }, purpose: "住宿搜索" });
    const c1 = frames[4].data as { ok: boolean; data: { items: { name: string; price: number }[]; cheapest: number } };
    expect(c1.ok).toBe(true);
    expect(c1.data.items[0]).toMatchObject({ name: "大理古城客栈", price: 180 });
    expect(frames[6].data).toMatchObject({ call_id: "c2", ok: false, data: null, degraded: "flyai_unavailable" });
    expect(frames[9].data).toMatchObject({ from: "research_positioning", to: "field_site_profiles" });
    expect(frames[10].data).toMatchObject({ reply_id: turn.turn_id, usage: { total_tokens: 42 } });
  });

  test("Last-Event-ID resumes from seq N+1", async () => {
    const created = await call(port, "POST", "/projects/p1/messages", { body: "{}" });
    const turn = JSON.parse(created.body) as CreateTurnResponse;
    const stream = await call(port, "GET", `/projects/p1/turns/${turn.turn_id}/events`, {
      headers: { "Last-Event-ID": "9" },
    });
    const frames = parseFrames(stream.body);
    expect(frames.map((f) => f.id)).toEqual([10, 11]);
    expect(frames.map((f) => f.event)).toEqual(["phase_transition", "turn_done"]);
  });

  test("replay is idempotent after script finished", async () => {
    const created = await call(port, "POST", "/projects/p1/messages", { body: "{}" });
    const turn = JSON.parse(created.body) as CreateTurnResponse;
    const url = `/projects/p1/turns/${turn.turn_id}/events`;
    const first = parseFrames((await call(port, "GET", url)).body);
    const second = parseFrames((await call(port, "GET", url)).body);
    expect(second).toEqual(first);
  });

  test("cancel returns cancelled status", async () => {
    const created = await call(port, "POST", "/projects/p1/messages", { body: "{}" });
    const turn = JSON.parse(created.body) as CreateTurnResponse;
    const cancelled = await call(port, "POST", `/projects/p1/turns/${turn.turn_id}/cancel`);
    expect(cancelled.status).toBe(200);
    expect(JSON.parse(cancelled.body)).toEqual({ turn_id: turn.turn_id, status: "cancelled" });
    const missing = await call(port, "POST", "/projects/p1/turns/turn_nope/cancel");
    expect(missing.status).toBe(404);
  });

  test("project fixture routes", async () => {
    const list = await call(port, "GET", "/projects");
    expect(list.status).toBe(200);
    expect(JSON.parse(list.body)[0].project_id).toBe("field_20261015_demo");

    const created = await call(port, "POST", "/projects", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ discipline: "人类学" }),
    });
    expect(created.status).toBe(201);
    expect(JSON.parse(created.body)).toMatchObject({ phase: "research_positioning" });

    const snapshot = await call(port, "GET", "/projects/p1");
    expect(snapshot.status).toBe(200);
    expect(JSON.parse(snapshot.body)).toMatchObject({ project_id: "p1", phase: "research_positioning" });

    const proposal = await call(port, "GET", "/projects/p1/proposal");
    expect(proposal.status).toBe(200);
    expect(JSON.parse(proposal.body)).toMatchObject({ kind: "proposal", mode: "global" });
  });
});
