/**
 * 两步式 mock SSE 服务器 — 对齐 Plan 3 契约（web_host/api/turns.py + web_host/sse.py）。
 *
 * - POST /projects/:id/messages            → {turn_id, status:"running"}（第一步）
 * - GET  /projects/:id/turns/:tid/events   → text/event-stream 脚本化回放（第二步），
 *   帧格式与 web_host/sse.py encode_event 完全一致：
 *   `id: {seq}\nevent: {name}\ndata: {turn_id,seq,...payload}\n\n`
 *   支持 Last-Event-ID 请求头从该 seq 之后补发。
 * - POST /projects/:id/turns/:tid/cancel   → {turn_id, status:"cancelled"}
 * - GET /projects、POST /projects、GET /projects/:id、GET /projects/:id/proposal
 *   → 固定假数据（够前端联调即可）
 *
 * 运行：npm run mock（tsx mock/sseServer.ts），默认端口 8301。
 * 也可被 vitest 导入：startMockServer(0, 5) 在临时端口起服务做冒烟。
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import process from "node:process";
import { pathToFileURL } from "node:url";

import type { EventType } from "../src/types/events";

export const PORT = 8301;
export const FRAME_DELAY_MS = 100;

// ---------------------------------------------------------------- 回合脚本

export interface ScriptFrame {
  event: EventType;
  payload: Record<string, unknown>;
}

interface TurnRecord {
  projectId: string;
  frames: ScriptFrame[];
  status: string;
}

/** turn_id → 回合脚本（内存 Map，进程重启即失）。 */
const turns = new Map<string, TurnRecord>();

/** 脚本化的一段完整演示回合（11 帧，seq 从 1 单调递增）。 */
export function buildScript(turnId: string): ScriptFrame[] {
  return [
    { event: "message_delta", payload: { text: "正在" } },
    { event: "message_delta", payload: { text: "为您搜索" } },
    { event: "message_delta", payload: { text: "大理住宿…" } },
    {
      event: "tool_started",
      payload: { call_id: "c1", tool: "search_hotels", args: { dest_name: "大理" }, purpose: "住宿搜索" },
    },
    {
      event: "tool_completed",
      payload: {
        call_id: "c1",
        ok: true,
        data: {
          items: [{ name: "大理古城客栈", price: 180, price_raw: "¥180" }],
          total: 1,
          cheapest: 180,
          dearest: 180,
        },
      },
    },
    {
      event: "tool_started",
      payload: { call_id: "c2", tool: "search_flights", args: { origin: "北京", destination: "大理" }, purpose: "" },
    },
    {
      event: "tool_completed",
      payload: { call_id: "c2", ok: false, data: null, degraded: "flyai_unavailable" },
    },
    { event: "message_delta", payload: { text: "已找到住宿，" } },
    { event: "message_delta", payload: { text: "航班搜索暂不可用。" } },
    {
      event: "phase_transition",
      payload: { from: "research_positioning", to: "field_site_profiles" },
    },
    { event: "turn_done", payload: { reply_id: turnId, usage: { total_tokens: 42 } } },
  ];
}

// ---------------------------------------------------------------- SSE 编码（对齐 sse.py）

/**
 * 与 web_host/sse.py encode_event 逐字符一致：
 * payload = {"turn_id", "seq", ...data} 平铺；JSON.stringify 不转义非 ASCII，
 * 等价于 json.dumps(..., ensure_ascii=False)。
 */
export function encodeFrame(turnId: string, seq: number, event: string, payload: Record<string, unknown>): string {
  const data = { turn_id: turnId, seq, ...payload };
  return `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** 与 sse.py parse_last_event_id 一致：非法值一律回落 0。 */
export function parseLastEventId(raw: string | string[] | undefined): number {
  const value = Array.isArray(raw) ? raw[0] : raw;
  const parsed = Number.parseInt(value ?? "0", 10);
  return Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
}

// ---------------------------------------------------------------- HTTP 工具

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Last-Event-ID",
};

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", ...CORS_HEADERS });
  res.end(JSON.stringify(body));
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.setEncoding("utf8");
    req.on("data", (chunk: string) => (raw += chunk));
    req.on("end", () => resolve(raw));
    req.on("error", reject);
  });
}

function newTurnId(): string {
  return `turn_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// ---------------------------------------------------------------- 假项目数据

const FAKE_CONTEXT = {
  discipline: "社会学",
  topic: "大理乡村旅居研究",
  purpose: "global",
  departure_city: "北京",
  target_location: "大理",
};

function fakeProjectList() {
  return [
    {
      project_id: "field_20261015_demo",
      phase: "research_positioning",
      created_at: "2026-10-15T09:00:00Z",
      updated_at: "2026-10-15T09:30:00Z",
      research_context: FAKE_CONTEXT,
      counts: { sites: 1, informants: 0, proposals: 0 },
    },
  ];
}

function fakeSnapshot(projectId: string) {
  return {
    project_id: projectId,
    phase: "research_positioning",
    research_context: FAKE_CONTEXT,
    field_sites: [{ name: "大理古城", confirmed: false, profile: null }],
    informants: [],
    proposals: [],
    phase_history: [{ from: "", to: "research_positioning", at: "2026-10-15T09:00:00Z" }],
    updated_at: "2026-10-15T09:30:00Z",
  };
}

function fakeProposal() {
  return {
    markdown: "# 研究提案（mock）\n\n## 研究定位\n\n大理乡村旅居研究。\n",
    kind: "proposal",
    version: "draft",
    sections: ["研究定位", "田野点"],
    requested_mode: "global",
    mode: "global",
    inputs_hash: "0000000000000000000000000000000000000000",
  };
}

// ---------------------------------------------------------------- SSE 回放

/**
 * 脚本化回放：每帧间隔 frameDelayMs 逐帧下发；客户端断开即停。
 * Last-Event-ID = N 时跳过 seq ≤ N 的帧（从 N+1 补发）；
 * 回合脚本已发完后重连（无 Last-Event-ID）则重放全部（幂等演示），
 * 对齐 Plan 3 turns.py 的 replay-after_seq 行为。脚本发完即关闭流。
 */
function streamTurn(res: ServerResponse, req: IncomingMessage, turnId: string, frames: ScriptFrame[], frameDelayMs: number): void {
  const lastEventId = parseLastEventId(req.headers["last-event-id"]);
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
    ...CORS_HEADERS,
  });

  let index = Math.min(Math.max(0, lastEventId), frames.length); // 首帧 seq = index+1 > lastEventId
  const timer = setInterval(() => {
    if (index >= frames.length) {
      clearInterval(timer);
      res.end();
      return;
    }
    const frame = frames[index];
    res.write(encodeFrame(turnId, index + 1, frame.event, frame.payload));
    index += 1;
  }, frameDelayMs);
  req.on("close", () => clearInterval(timer));
}

// ---------------------------------------------------------------- 路由

const RE_MESSAGES = /^\/projects\/([^/]+)\/messages$/;
const RE_EVENTS = /^\/projects\/([^/]+)\/turns\/([^/]+)\/events$/;
const RE_CANCEL = /^\/projects\/([^/]+)\/turns\/([^/]+)\/cancel$/;
const RE_PROPOSAL = /^\/projects\/([^/]+)\/proposal$/;
const RE_PROJECT = /^\/projects\/([^/]+)$/;

async function handleRequest(req: IncomingMessage, res: ServerResponse, frameDelayMs: number): Promise<void> {
  const method = req.method ?? "GET";
  const path = new URL(req.url ?? "/", `http://localhost:${PORT}`).pathname;

  if (method === "OPTIONS") {
    res.writeHead(204, CORS_HEADERS);
    res.end();
    return;
  }

  let m: RegExpMatchArray | null;

  if (method === "POST" && (m = RE_MESSAGES.exec(path))) {
    await readBody(req); // 请求体 {text, idempotency_key?} 对 mock 无实际作用
    const projectId = decodeURIComponent(m[1]);
    const turnId = newTurnId();
    turns.set(turnId, { projectId, frames: buildScript(turnId), status: "running" });
    sendJson(res, 201, { turn_id: turnId, status: "running" });
    return;
  }

  if (method === "GET" && (m = RE_EVENTS.exec(path))) {
    const turnId = decodeURIComponent(m[2]);
    const record = turns.get(turnId);
    // 未知 tid 也现编一份脚本（演示容错，方便直接 GET 冒烟）
    streamTurn(res, req, turnId, record ? record.frames : buildScript(turnId), frameDelayMs);
    return;
  }

  if (method === "POST" && (m = RE_CANCEL.exec(path))) {
    const turnId = decodeURIComponent(m[2]);
    const record = turns.get(turnId);
    if (!record) {
      sendJson(res, 404, { detail: "回合不存在" });
      return;
    }
    record.status = "cancelled";
    sendJson(res, 200, { turn_id: turnId, status: "cancelled" });
    return;
  }

  if (method === "GET" && (m = RE_PROPOSAL.exec(path))) {
    sendJson(res, 200, fakeProposal());
    return;
  }

  if (method === "GET" && path === "/projects") {
    sendJson(res, 200, fakeProjectList());
    return;
  }

  if (method === "POST" && path === "/projects") {
    let body: { project_id?: string } = {};
    try {
      body = JSON.parse((await readBody(req)) || "{}");
    } catch {
      body = {};
    }
    const projectId = body.project_id || `field_${Date.now()}`;
    sendJson(res, 201, { project_id: projectId, phase: "research_positioning" });
    return;
  }

  if (method === "GET" && (m = RE_PROJECT.exec(path))) {
    sendJson(res, 200, fakeSnapshot(decodeURIComponent(m[1])));
    return;
  }

  sendJson(res, 404, { detail: `未实现的路由: ${method} ${path}` });
}

// ---------------------------------------------------------------- 启动

/** 起 mock 服务器；port=0 时用系统分配的临时端口（测试友好）。 */
export function startMockServer(port: number = PORT, frameDelayMs: number = FRAME_DELAY_MS): Promise<Server> {
  return new Promise((resolve, reject) => {
    const server = createServer((req, res) => {
      handleRequest(req, res, frameDelayMs).catch((err) => {
        if (!res.headersSent) sendJson(res, 500, { detail: String(err) });
      });
    });
    server.once("error", reject);
    server.listen(port, () => resolve(server));
  });
}

// tsx 直接运行本文件时自动启动（被 vitest 导入时不启动）
const scriptPath = process.argv[1];
const invokedDirectly = scriptPath !== undefined && import.meta.url === pathToFileURL(scriptPath).href;

if (invokedDirectly) {
  startMockServer().then(
    () => console.log(`[mock] SSE server listening on http://localhost:${PORT}`),
    (err) => {
      console.error(`[mock] failed to start on :${PORT}`, err);
      process.exit(1);
    },
  );
}
