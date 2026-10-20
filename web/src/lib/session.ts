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

/** 随机令牌：crypto.randomUUID 去连字符（128 bit）；无 crypto 时 Math.random 拼 16 字节 hex。 */
export function randomToken(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return Array.from({ length: 16 }, () =>
    Math.floor(Math.random() * 256).toString(16).padStart(2, "0")).join("");
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
export function setSessionToken(t: string): void { writeKey(SESSION_TOKEN_KEY, t); }
export function clearSessionToken(): void { writeKey(SESSION_TOKEN_KEY, ""); }

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
