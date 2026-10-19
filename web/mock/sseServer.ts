/**
 * 两步式 mock SSE 服务器 — 对齐 Plan 3 契约（web_host/api/turns.py + web_host/sse.py）。
 *
 * - POST /projects/:id/messages            → {turn_id, status:"running"}（第一步）
 * - GET  /projects/:id/turns/:tid/events   → text/event-stream 脚本化回放（第二步），
 *   帧格式与 web_host/sse.py encode_event 完全一致：
 *   `id: {seq}\nevent: {name}\ndata: {turn_id,seq,...payload}\n\n`
 *   支持 Last-Event-ID 请求头从该 seq 之后补发。
 * - POST /projects/:id/turns/:tid/cancel   → {turn_id, status:"cancelled"}
 * - GET  /projects/:id/messages             → 消息史回放（内存；assistant 带 quick_replies 键，
 *   B 节刷新恢复，对齐 turns.py::list_messages 只透出该键的约定）
 * - GET /projects、POST /projects、GET /projects/:id、GET /projects/:id/proposal
 *   → 固定假数据（够前端联调即可）
 * - GET /projects/:id/proposals            → 版本列表（Task 8 rest.listProposals 消费）
 * - GET  /projects/:id/proposal/download    → 三格式附件（?format=md|docx|txt，默认 md）
 * - GET  /settings/status                    → key 配置状态（web_host/api/settings.py 同构；
 *   默认 configured:true 建模阿里云部署形态）；POST /__test/settings 供 e2e 翻转
 * - POST /projects/:id/messages 带 403 门（服务端未配且无 X-DashScope-Key 头 → llm_key_required，
 *   web_host/api/turns.py::_resolve_clients 同构）
 * - POST /auth/register|login → {session_token,user,claim}（账号隔离一期：建账号/发会话 +
 *   认领匿名项目 owner 切 user:<id>）；POST /auth/logout → 204；GET /auth/me → 会话/匿名身份。
 *   错误体统一 FastAPI detail 形状 {detail:{code,message}}：已存邮箱 400 email_taken、
 *   凭据不符 401 invalid_credentials、失效会话 401 session_expired（顶部会话守卫统一拦截）。
 *
 * 运行：npm run mock（tsx mock/sseServer.ts），端口取 env MOCK_PORT，默认 8301；
 * e2e 用 MOCK_PORT=8300 匹配 vite proxy（playwright.config.ts webServer）。
 * 也可被 vitest 导入：startMockServer(0, 5) 在临时端口起服务做冒烟。
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import process from "node:process";
import { pathToFileURL } from "node:url";

import type { EventType } from "../src/types/events";

export const PORT = Number(process.env.MOCK_PORT ?? 8301);
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

/** 演示回合的快捷作答选项（B 节 [[options]] 契约，与尾帧标记一致）。 */
export const QUICK_REPLIES = ["换家客栈", "先定行程", "换个城市"];

/** 演示回合的最终 assistant 正文（后端剥离 [[options]] 标记后入史的形态）。 */
const ASSISTANT_REPLY = "正在为您搜索大理住宿…已找到住宿，航班搜索暂不可用。";

/** project_id → 消息史（内存，进程重启即失）：GET /messages 回放用（B3 刷新恢复）。 */
const messageLog = new Map<
  string,
  Array<{ role: "user" | "assistant"; content: string; quick_replies?: string[] }>
>();

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
    // N7 冒烟演示帧（PoC2 §5.2）：画像卡带 geo/facilities（C4 双源 + 地图块），
    // 行程卡带 last_mile（N2 末端接驳行 + N6 接驳示意地图）；坐标取昆明/喜洲真值
    {
      event: "tool_started",
      payload: { call_id: "c3", tool: "profile_field_site", args: { location: "喜洲镇" }, purpose: "田野点画像" },
    },
    {
      event: "tool_completed",
      payload: {
        call_id: "c3",
        ok: true,
        data: {
          location: "喜洲镇",
          geo: { location: "100.1961,25.0473", province: "云南省" },
          facilities: { items: [
            { name: "喜洲卫生院", extra: { location: "100.2005,25.0487", facility_kind: "卫生院 诊所" } },
            { name: "喜洲镇中心学校", extra: { location: "100.1902,25.0601", facility_kind: "学校" } },
          ], total: 2 },
          transport: null, accommodation: null, culture: [], amenities: null,
          summary: { section_counts: {}, accommodation_nightly_min: null, transport_min: null, hints: {} },
          generated_at: "2026-10-18T08:00:00",
        },
      },
    },
    {
      event: "tool_started",
      payload: { call_id: "c4", tool: "compose_itinerary", args: { departure: "昆明", destinations: ["喜洲"], days: 90 }, purpose: "行程编排" },
    },
    {
      event: "tool_completed",
      payload: {
        call_id: "c4",
        ok: true,
        data: {
          departure: "昆明", destinations: ["喜洲"],
          start_date: "2026-06-01", end_date: "2026-08-29",
          legs: [{
            from: "昆明", to: "喜洲", date: "2026-06-01", direction: "outbound",
            chosen: { name: "MU5843", price: 620, mode: "flight", price_raw: "¥620", extra: {} },
            alternatives: [], note: "",
            last_mile: {
              hub_geo: { location: "102.7126,25.0389", city: "昆明" },
              site_geo: { location: "100.1961,25.0473", city: "喜洲" },
              options: {
                items: [{ name: "8路", price: 8, schedule: { duration_minutes: 60 } }],
                total: 2, cheapest: 8, dearest: 12,
              },
              note: "",
            },
          }],
          stays: [{
            dest: "喜洲", check_in: "2026-06-01", check_out: "2026-08-29", nights: 89,
            nightly_price: 150, chosen: { name: "喜洲客栈", price: 150, mode: "民宿", extra: {} },
            alternatives: [], long_stay_unverified: true,
          }],
          rhythm: [
            { label: "适应期", days: 9, note: "报到、熟悉村落格局" },
            { label: "深入期", days: 45, note: "参与式观察与深度访谈" },
            { label: "补充期", days: 24, note: "补访缺口、整理笔记" },
          ],
          buffer_days: 9,
          alternatives: [{ from: "昆明", to: "喜洲", items: [{ name: "G2937", price: 350, mode: "train", price_raw: "¥350", extra: {} }] }],
          budget: {
            items: [
              { name: "交通（昆明→喜洲）", amount: 620, source: "cheapest", currency: "CNY", unit: "票价×人数", quantity: 1, people: 1, priced_at: "2026-10-18T08:00:00", source_ref: "MU5843" },
              { name: "住宿（喜洲）", amount: 13500, source: "cheapest", currency: "CNY", unit: "每晚价×夜数×房间数", quantity: 90, people: 1, priced_at: "2026-10-18T08:00:00", source_ref: "喜洲客栈" },
            ],
            total: 14120, budget_total: 20000, over_budget: false, coverage: "complete", uncovered: [],
          },
          generated_at: "2026-10-18T08:00:00",
        },
      },
    },
    { event: "message_delta", payload: { text: "已找到住宿，" } },
    // 尾帧带 [[options]] 标记（B 节契约）：流式期间前端 strip 半截标记防闪烁，
    // turn_done 后 store 剥离入史 + chips 上场
    {
      event: "message_delta",
      payload: { text: "航班搜索暂不可用。\n\n[[options]]换家客栈|先定行程|换个城市[[/options]]" },
    },
    {
      event: "phase_transition",
      payload: { from: "research_positioning", to: "field_site_profiles" },
    },
    // turn_done 携带 quick_replies（对齐 loop.py 轮末抽取后的 payload 形状）
    {
      event: "turn_done",
      payload: { reply_id: turnId, usage: { total_tokens: 42 }, quick_replies: QUICK_REPLIES },
    },
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
  "Access-Control-Allow-Headers": "Content-Type, Last-Event-ID, X-DashScope-Key, X-FlyAI-Key, X-Owner-Token",
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

/** POST body 解析：非 JSON/空体 → {}（联调容错，坏 body 不崩路由）。 */
async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  try {
    return JSON.parse((await readBody(req)) || "{}") as Record<string, unknown>;
  } catch {
    return {};
  }
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

// ---------------------------------------------------------------- 归属存储（spec 对齐：创建需令牌 + 列表过滤 + 404 隔离）

interface StoredProject { id: string; owner: string; }

/** 内存项目登记：id → 归属（anon:<token> 或登录认领后 user:<id>）。mock 轻量——只记归属，内容恒返回假数据。 */
const projects = new Map<string, StoredProject>();

// ---------------------------------------------------------------- 账号与会话（账号隔离一期 /auth 四端点）

interface StoredUser { userId: string; email: string; password: string; createdAt: string; }

/** 内存用户表：email → 用户（进程重启即失；mock 不做密码哈希，仅联调用）。 */
const users = new Map<string, StoredUser>();

/** 内存会话表：sess_ 令牌 → userId（登录态归属解析 + GET /auth/me 消费）。 */
const sessions = new Map<string, string>();

function newSessionToken(): string {
  return `sess_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

/** 认领（spec「匿名项目划归用户」）：owner === anon:<anon_token> 的项目 owner 改 user:<userId>。 */
function claimAnonProjects(anonToken: string | undefined, userId: string): { claimed: number; skipped: number } {
  let claimed = 0;
  if (anonToken) {
    const anonOwner = `anon:${anonToken}`;
    for (const rec of projects.values()) {
      if (rec.owner === anonOwner) {
        rec.owner = `user:${userId}`;
        claimed += 1;
      }
    }
  }
  return { claimed, skipped: 0 };
}

/** 与后端 owner_from_request 同构：裸 token 归一化 anon:<token>；登录态 sess_ 令牌经
 *  会话表解析为 user:<userId>（失效会话由 handleRequest 顶部守卫拦 401，走不到这里）；
 *  缺失/空 → null。 */
function ownerFromHeaders(req: IncomingMessage): string | null {
  const raw = String(req.headers["x-owner-token"] ?? "").trim();
  if (raw === "") return null;
  if (raw.startsWith("sess_")) {
    const userId = sessions.get(raw);
    return userId ? `user:${userId}` : null;
  }
  return `anon:${raw}`;
}

/** 项目归属守卫：不存在/归属不符/无令牌 → 404（不泄露存在性，与后端 checked_owned_project 同形）。 */
function ownedProject(res: ServerResponse, req: IncomingMessage, projectId: string): boolean {
  const owner = ownerFromHeaders(req);
  const rec = projects.get(projectId);
  if (!owner || !rec || rec.owner !== owner) {
    sendJson(res, 404, { detail: `项目不存在: ${projectId}` });
    return false;
  }
  return true;
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

/**
 * 八章提案（Task 9）：markdown 含八章 `## ` 标题，供 ChapterNav 滚动高亮 +
 * ProposalViewer 渲染 + L4 端到端断言；id/version 与 /proposals 版本列表的
 * final 条目（doc_2）联动，保证 VersionSwitch 当前项高亮一致。
 */
function fakeProposal() {
  const chapters = [
    "一 · 选题依据", "二 · 田野点选择与可行性", "三 · 研究设计与方法论",
    "四 · 详细行程安排", "五 · 实时报价与预算", "六 · 风险评估与伦理预案",
    "七 · 预期成果", "八 · 参考文献与附录",
  ];
  const markdown = "# 白族村落宗族组织变迁研究（mock）\n\n" +
    chapters.map((c, i) => `## ${c}\n\n这是第 ${i + 1} 章正文（mock 数据，用于 L4 端到端冒烟）。\n`).join("\n");
  return { id: "doc_2", markdown, kind: "proposal", version: "final", sections: chapters,
           requested_mode: "global", mode: "global",
           inputs_hash: "0000000000000000000000000000000000000000" };
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

// ---------------------------------------------------------------- key 配置状态（e2e 可翻转）

/** 模拟阿里云部署形态：服务端环境变量已配 key（GET /settings/status 消费）。
 *  e2e 用 POST /__test/settings 翻转后须恢复，不污染同进程后续用例。 */
let settingsState: {
  dashscope_configured: boolean;
  flyai_configured: boolean;
  amap_configured: boolean;
  amap_js_configured: boolean;
  amap_js_key: string;
  amap_js_security: string;
} = {
  dashscope_configured: true,
  flyai_configured: true,
  amap_configured: true,
  // N6 地图面板链（settings.py 同构）：mock 下发占位 JS Key，面板可真加载 loader
  amap_js_configured: true,
  amap_js_key: "mock-js-key",
  amap_js_security: "mock-js-security",
};

// ---------------------------------------------------------------- 路由

const RE_MESSAGES = /^\/projects\/([^/]+)\/messages$/;
const RE_EVENTS = /^\/projects\/([^/]+)\/turns\/([^/]+)\/events$/;
const RE_CANCEL = /^\/projects\/([^/]+)\/turns\/([^/]+)\/cancel$/;
const RE_PROPOSALS = /^\/projects\/([^/]+)\/proposals$/;
const RE_DOWNLOAD = /^\/projects\/([^/]+)\/proposal\/download$/;
const RE_BY_ID_DOWNLOAD = /^\/projects\/([^/]+)\/proposals\/([^/]+)\/download$/;
const RE_PROPOSAL = /^\/projects\/([^/]+)\/proposal$/;
const RE_PROJECT = /^\/projects\/([^/]+)$/;

async function handleRequest(req: IncomingMessage, res: ServerResponse, frameDelayMs: number): Promise<void> {
  const method = req.method ?? "GET";
  const url = new URL(req.url ?? "/", `http://localhost:${PORT}`);
  const path = url.pathname;

  if (method === "OPTIONS") {
    res.writeHead(204, CORS_HEADERS);
    res.end();
    return;
  }

  // 登录会话守卫：X-Owner-Token 携带失效的 sess_ → 401 session_expired（FastAPI detail
  // 形状，前端 isSessionExpiredError 解析 detail.code——R15 失效恢复链路的数据源）。
  // 登录/注册/登出豁免：登录正是换发新会话的通道，不能被旧会话失效拦在门外。
  const rawOwnerToken = String(req.headers["x-owner-token"] ?? "").trim();
  const isAuthWritePath = path === "/auth/register" || path === "/auth/login" || path === "/auth/logout";
  if (rawOwnerToken.startsWith("sess_") && !sessions.has(rawOwnerToken) && !isAuthWritePath) {
    sendJson(res, 401, { detail: { code: "session_expired", message: "登录会话已失效，请重新登录" } });
    return;
  }

  let m: RegExpMatchArray | null;

  // ---- /auth（账号隔离一期，语义对齐生产 web_host/api/owner.py）----

  // POST /auth/register → 建用户 + 发会话 + 认领匿名项目（owner 切 user:<id>）
  if (method === "POST" && path === "/auth/register") {
    const body = await readJsonBody(req);   // {email, password, anon_token?}
    const email = String(body.email ?? "").trim();
    if (users.has(email)) {
      sendJson(res, 400, { detail: { code: "email_taken", message: "该邮箱已注册，请直接登录" } });
      return;
    }
    const user: StoredUser = {
      userId: `user_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
      email,
      password: String(body.password ?? ""),
      createdAt: new Date().toISOString(),
    };
    users.set(email, user);
    const sessionToken = newSessionToken();
    sessions.set(sessionToken, user.userId);
    sendJson(res, 200, {
      session_token: sessionToken,
      user: { user_id: user.userId, email: user.email, created_at: user.createdAt },
      claim: claimAnonProjects(typeof body.anon_token === "string" ? body.anon_token : undefined, user.userId),
    });
    return;
  }

  // POST /auth/login → 校验凭据 + 发会话 + 认领；不符 401 invalid_credentials
  // （邮箱不存在与密码错误同一句话，不泄露注册面）
  if (method === "POST" && path === "/auth/login") {
    const body = await readJsonBody(req);
    const user = users.get(String(body.email ?? "").trim());
    if (!user || user.password !== String(body.password ?? "")) {
      sendJson(res, 401, { detail: { code: "invalid_credentials", message: "邮箱或密码不正确" } });
      return;
    }
    const sessionToken = newSessionToken();
    sessions.set(sessionToken, user.userId);
    sendJson(res, 200, {
      session_token: sessionToken,
      user: { user_id: user.userId, email: user.email, created_at: user.createdAt },
      claim: claimAnonProjects(typeof body.anon_token === "string" ? body.anon_token : undefined, user.userId),
    });
    return;
  }

  // POST /auth/logout → 删会话 → 204（幂等：令牌未命中也 204，前端 finally 清本地）
  if (method === "POST" && path === "/auth/logout") {
    const raw = String(req.headers["x-owner-token"] ?? "").trim();
    if (raw.startsWith("sess_")) sessions.delete(raw);
    res.writeHead(204, CORS_HEADERS);
    res.end();
    return;
  }

  // GET /auth/me → 会话身份：sess_ 命中 → {anonymous:false,user}；其余（匿名/无令牌）→
  // {anonymous:true}。sess_ 失效分支由顶部会话守卫统一 401 session_expired。
  if (method === "GET" && path === "/auth/me") {
    const raw = String(req.headers["x-owner-token"] ?? "").trim();
    const userId = raw.startsWith("sess_") ? sessions.get(raw) : undefined;
    const user = userId ? [...users.values()].find((u) => u.userId === userId) : undefined;
    if (user) {
      sendJson(res, 200, { anonymous: false, user: { user_id: user.userId, email: user.email, created_at: user.createdAt } });
    } else {
      sendJson(res, 200, { anonymous: true });
    }
    return;
  }

  // GET /settings/status → key 配置状态（只回布尔不回明文）
  if (method === "GET" && path === "/settings/status") {
    sendJson(res, 200, settingsState);
    return;
  }

  // POST /__test/settings → 翻转配置状态（仅 e2e 用；body 部分覆盖）
  if (method === "POST" && path === "/__test/settings") {
    let body: Record<string, unknown> = {};
    try {
      body = JSON.parse((await readBody(req)) || "{}");
    } catch {
      body = {};
    }
    settingsState = { ...settingsState, ...body };
    sendJson(res, 200, settingsState);
    return;
  }

  // GET /projects/:id/messages → 消息史回放（B3 刷新恢复：只透出 quick_replies 键）
  if (method === "GET" && (m = RE_MESSAGES.exec(path))) {
    sendJson(res, 200, messageLog.get(decodeURIComponent(m[1])) ?? []);
    return;
  }

  if (method === "POST" && (m = RE_MESSAGES.exec(path))) {
    const rawBody = await readBody(req); // {text, idempotency_key?}；text 入消息史
    const projectId = decodeURIComponent(m[1]);
    if (!ownedProject(res, req, projectId)) return;
    // 403 门（同构 turns.py::_resolve_clients）：服务端未配且请求头未带页面 key
    const pageKey = String(req.headers["x-dashscope-key"] ?? "").trim();
    if (!settingsState.dashscope_configured && !pageKey) {
      sendJson(res, 403, {
        detail: {
          code: "llm_key_required",
          message: "未配置百炼 API Key：请在首页「API 密钥」卡填写并保存",
        },
      });
      return;
    }
    // 消息史入账：user 即问即记；assistant 剥离标记后带 quick_replies 入史（后端形态）
    let text = "";
    try {
      text = String((JSON.parse(rawBody || "{}") as { text?: string }).text ?? "");
    } catch {
      text = "";
    }
    const history = messageLog.get(projectId) ?? [];
    history.push({ role: "user", content: text });
    history.push({ role: "assistant", content: ASSISTANT_REPLY, quick_replies: QUICK_REPLIES });
    messageLog.set(projectId, history);
    const turnId = newTurnId();
    turns.set(turnId, { projectId, frames: buildScript(turnId), status: "running" });
    sendJson(res, 201, { turn_id: turnId, status: "running" });
    return;
  }

  if (method === "GET" && (m = RE_EVENTS.exec(path))) {
    if (!ownedProject(res, req, decodeURIComponent(m[1]))) return;
    const turnId = decodeURIComponent(m[2]);
    const record = turns.get(turnId);
    // 未知 tid 也现编一份脚本（演示容错，方便直接 GET 冒烟）
    streamTurn(res, req, turnId, record ? record.frames : buildScript(turnId), frameDelayMs);
    return;
  }

  if (method === "POST" && (m = RE_CANCEL.exec(path))) {
    if (!ownedProject(res, req, decodeURIComponent(m[1]))) return;
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

  // GET /projects/:id/proposals → 版本列表（Task 8 rest.listProposals 消费）
  if (method === "GET" && (m = RE_PROPOSALS.exec(path))) {
    if (!ownedProject(res, req, decodeURIComponent(m[1]))) return;
    sendJson(res, 200, [
      { id: "doc_1", kind: "proposal", version: "draft", created_at: "2026-10-15T09:00:00Z" },
      { id: "doc_2", kind: "proposal", version: "final", created_at: "2026-10-15T10:00:00Z" },
    ]);
    return;
  }

  // GET /projects/:id/proposal/download → 三格式附件（?format=md|docx|txt，默认 md）。
  // 路由顺序红线：必须在 RE_PROPOSAL（/proposal）之前匹配，否则被其截胡。
  // mock 职责边界：只复刻后端的状态码/响应头/文件名形状（docx 返回 PK 魔数、txt 轻量去标记），不引入 python-docx 逻辑。
  if (method === "GET" && (m = RE_DOWNLOAD.exec(path))) {
    if (!ownedProject(res, req, decodeURIComponent(m[1]))) return;
    const doc = fakeProposal();
    const format = url.searchParams.get("format") ?? "md";
    if (format === "docx") {
      res.writeHead(200, {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": `attachment; filename="proposal.docx"`,
        ...CORS_HEADERS,
      });
      res.end(Buffer.from("PK\u0003\u0004mock docx"));
      return;
    }
    if (format === "txt") {
      res.writeHead(200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": `attachment; filename="proposal.txt"`,
        ...CORS_HEADERS,
      });
      res.end(doc.markdown.replace(/^#+\s*/gm, "").replace(/[*`]/g, ""));
      return;
    }
    res.writeHead(200, {
      "Content-Type": "text/markdown; charset=utf-8",
      "Content-Disposition": `attachment; filename="proposal.md"`,
      ...CORS_HEADERS,
    });
    res.end(doc.markdown);
    return;
  }

  // GET /projects/:id/proposals/:docId/download → 三格式附件（by-id 端点，ProposalView 主入口）。
  if (method === "GET" && (m = RE_BY_ID_DOWNLOAD.exec(path))) {
    if (!ownedProject(res, req, decodeURIComponent(m[1]))) return;
    const doc = fakeProposal();
    const format = url.searchParams.get("format") ?? "md";
    if (format === "docx") {
      res.writeHead(200, {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": `attachment; filename="proposal.docx"`,
        ...CORS_HEADERS,
      });
      res.end(Buffer.from("PK\u0003\u0004mock docx"));
      return;
    }
    if (format === "txt") {
      res.writeHead(200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": `attachment; filename="proposal.txt"`,
        ...CORS_HEADERS,
      });
      res.end(doc.markdown.replace(/^#+\s*/gm, "").replace(/[*`]/g, ""));
      return;
    }
    res.writeHead(200, {
      "Content-Type": "text/markdown; charset=utf-8",
      "Content-Disposition": `attachment; filename="proposal.md"`,
      ...CORS_HEADERS,
    });
    res.end(doc.markdown);
    return;
  }

  if (method === "GET" && (m = RE_PROPOSAL.exec(path))) {
    if (!ownedProject(res, req, decodeURIComponent(m[1]))) return;
    sendJson(res, 200, fakeProposal());
    return;
  }

  if (method === "GET" && path === "/projects") {
    const owner = ownerFromHeaders(req);
    if (!owner) {
      sendJson(res, 200, []);
      return;
    }
    sendJson(res, 200, [...projects.values()].filter((p) => p.owner === owner).map((p) => ({
      project_id: p.id,
      phase: "research_positioning",
      created_at: "2026-10-15T09:00:00Z",
      updated_at: "2026-10-15T09:30:00Z",
      research_context: FAKE_CONTEXT,
      counts: { sites: 1, informants: 0, proposals: 0 },
    })));
    return;
  }

  if (method === "POST" && path === "/projects") {
    let body: { project_id?: string } = {};
    try {
      body = JSON.parse((await readBody(req)) || "{}");
    } catch {
      body = {};
    }
    const owner = ownerFromHeaders(req);
    if (!owner) {
      sendJson(res, 400, {
        detail: { code: "owner_token_required", message: "创建项目需携带 X-Owner-Token 头" },
      });
      return;
    }
    const projectId = body.project_id || `field_${Date.now()}`;
    projects.set(projectId, { id: projectId, owner });
    sendJson(res, 201, { project_id: projectId, phase: "research_positioning" });
    return;
  }

  if (method === "GET" && (m = RE_PROJECT.exec(path))) {
    const pid = decodeURIComponent(m[1]);
    if (!ownedProject(res, req, pid)) return;
    sendJson(res, 200, fakeSnapshot(pid));
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
