// web/src/lib/session.ts
/**
 * 匿名归属令牌（spec「数据模型/前端」）——浏览器首次使用时生成随机 token，
 * 此后所有 /projects 请求经 ownerHeaders() 统一携带 X-Owner-Token；服务端
 * （owner_from_request）归一化为 anon:<token> 做数据归属隔离。与 keys.ts 同法：
 * 令牌只存浏览器本地，不落服务端；localStorage 不可用（隐私模式）时降级内存
 * Map，保证同一页面会话内归属稳定。
 *
 * 登录会话令牌（SESSION_TOKEN_KEY）：登录后由 /auth 响应写入，ownerHeaders()
 * 优先携带（登录态归属）；登出/失效时清除，回落匿名令牌。
 */
const OWNER_TOKEN_KEY = "fieldwork.owner_token";
const SESSION_TOKEN_KEY = "fieldwork.session_token";

/** localStorage 不可用时的会话内兜底（同一 JS 生命周期内保持一致）。 */
const memoryFallback = new Map<string, string>();

function readKey(key: string): string {
  try { return (localStorage.getItem(key) || "").trim(); }
  catch { return memoryFallback.get(key) ?? ""; }
}
function writeKey(key: string, val: string): void {
  val = val.trim();   // 与读侧对称（keys.ts saveKeys 同法）：空白串视同空值删 key
  try { if (val) localStorage.setItem(key, val); else localStorage.removeItem(key); }
  catch { if (val) memoryFallback.set(key, val); else memoryFallback.delete(key); }
}

/** 随机令牌（32 位 hex）：randomUUID 优先，其次 getRandomValues。
 *  两者都不可用时 **fail-closed**——归属令牌是匿名项目数据的唯一凭据，
 *  Math.random 不满足密码学随机性（Q05），用它等于让令牌可被猜中。 */
export function randomToken(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID().replace(/-/g, "");
  }
  if (c && typeof c.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    c.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  throw new Error("安全随机源不可用：无法生成归属令牌；请使用支持 Web Crypto 的浏览器");
}

/** 懒生成：首次调用生成并持久化，之后稳定返回同一 token（清存储即换身份，匿名语义）。 */
export function getOwnerToken(): string {
  const existing = readKey(OWNER_TOKEN_KEY);
  if (existing) return existing;
  const token = randomToken();
  writeKey(OWNER_TOKEN_KEY, token);
  return token;
}

export function getSessionToken(): string { return readKey(SESSION_TOKEN_KEY); }

// 认证代次（评审 T03）：会话令牌每次写入/清除即自增。请求发起时捕获代次，
// 只有仍属当前代次的 401 才允许触发全局会话清理——迟到的旧身份 401 可以
// 向自己的调用方报错，但不得清掉后来建立的会话（如换号后的 Bob）。
let authEpoch = 0;
export function getAuthEpoch(): number { return authEpoch; }

/** 延迟响应的身份归属：发起时用的是 ``sentToken``，若当前已换成**另一个非空
 *  会话**（换号登录），则该响应属于旧身份。令牌被清空（登出/会话失效）不算
 *  换号——同一身份的两个并发 401 都应被当作当前会话处理。 */
export function isStaleSession(sentToken: string): boolean {
  const current = getSessionToken();
  return current !== "" && current !== sentToken;
}

export function setSessionToken(t: string): void {
  writeKey(SESSION_TOKEN_KEY, t);
  authEpoch += 1;
}
export function clearSessionToken(): void {
  writeKey(SESSION_TOKEN_KEY, "");
  authEpoch += 1;
}

/** 请求头注入：会话令牌优先（登录态），否则懒生成匿名令牌——归属必须携带（与 authHeaders 的「空值不带头」不同）。 */
export function ownerHeaders(): Record<string, string> {
  return { "X-Owner-Token": getSessionToken() || getOwnerToken() };   // 登录态优先，回落匿名
}

// ── 记住邮箱（登录弹层改版）──

const REMEMBERED_EMAIL_KEY = "fieldwork.remembered_email";

/** 已记住的邮箱（登录弹层打开时预填），无则空串。 */
export function readRememberedEmail(): string { return readKey(REMEMBERED_EMAIL_KEY); }
/** 登录成功且勾选「记住邮箱」时写入——只存邮箱，永不存密码。 */
export function writeRememberedEmail(email: string): void { writeKey(REMEMBERED_EMAIL_KEY, email); }
/** 登录成功但未勾选时清除（writeKey 空串即删 key，与读侧对称）。 */
export function clearRememberedEmail(): void { writeKey(REMEMBERED_EMAIL_KEY, ""); }
