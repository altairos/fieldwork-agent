// web/src/lib/session.ts
/**
 * 匿名归属令牌（spec「数据模型/前端」）——浏览器首次使用时生成随机 token，
 * 此后所有 /projects 请求经 ownerHeaders() 统一携带 X-Owner-Token；服务端
 * （owner_from_request）归一化为 anon:<token> 做数据归属隔离。与 keys.ts 同法：
 * 令牌只存浏览器本地，不落服务端；localStorage 不可用（隐私模式）时降级内存
 * Map，保证同一页面会话内归属稳定。
 */
const OWNER_TOKEN_KEY = "fieldwork.owner_token";

/** localStorage 不可用时的会话内兜底（同一 JS 生命周期内保持一致）。 */
const memoryFallback = new Map<string, string>();

function readStored(): string {
  try {
    return (localStorage.getItem(OWNER_TOKEN_KEY) || "").trim();
  } catch {
    return memoryFallback.get(OWNER_TOKEN_KEY) ?? "";
  }
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
  const existing = readStored();
  if (existing) return existing;
  const token = randomToken();
  try {
    localStorage.setItem(OWNER_TOKEN_KEY, token);
  } catch {
    memoryFallback.set(OWNER_TOKEN_KEY, token);
  }
  return token;
}

/** 请求头注入：懒生成保证恒有值（与 authHeaders 的「空值不带头」不同——归属必须携带）。 */
export function ownerHeaders(): Record<string, string> {
  return { "X-Owner-Token": getOwnerToken() };
}
