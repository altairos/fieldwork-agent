/** 页面级 API key：localStorage 读写 + 请求头注入。

 * 首页「API 密钥」卡保存；rest.ts 每次请求自动带 X-DashScope-Key /
 * X-FlyAI-Key（后端 web_host/api/turns.py 读取，页面 key 优先于服务端单例）。
 * key 只存在浏览器本地，不落服务端。
 */

const DASHSCOPE_KEY = "fieldwork.dashscope_key";
const FLYAI_KEY = "fieldwork.flyai_key";

export interface ApiKeys {
  dashscope: string;
  flyai: string;
}

function read(name: string): string {
  try {
    return (localStorage.getItem(name) || "").trim();
  } catch {
    return ""; // localStorage 不可用（隐私模式等）时按无 key 处理
  }
}

export function getKeys(): ApiKeys {
  return { dashscope: read(DASHSCOPE_KEY), flyai: read(FLYAI_KEY) };
}

export function hasDashScopeKey(): boolean {
  return read(DASHSCOPE_KEY) !== "";
}

export function saveKeys(dashscope: string, flyai: string): void {
  const d = dashscope.trim();
  if (d) localStorage.setItem(DASHSCOPE_KEY, d);
  else localStorage.removeItem(DASHSCOPE_KEY); // 留空即移除，回落服务端配置
  const f = flyai.trim();
  if (f) localStorage.setItem(FLYAI_KEY, f);
  else localStorage.removeItem(FLYAI_KEY);
}

export function clearKeys(): void {
  localStorage.removeItem(DASHSCOPE_KEY);
  localStorage.removeItem(FLYAI_KEY);
}

/** 注入到 fetch headers 的鉴权头；空 key 不带头（后端回落到启动单例）。 */
export function authHeaders(): Record<string, string> {
  const { dashscope, flyai } = getKeys();
  const h: Record<string, string> = {};
  if (dashscope) h["X-DashScope-Key"] = dashscope;
  if (flyai) h["X-FlyAI-Key"] = flyai;
  return h;
}
