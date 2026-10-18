/** 页面级 API key 与访问令牌：localStorage 读写 + 请求头注入。
 *
 * 首页「API 密钥」卡保存；rest.ts / sse.ts 每次请求自动带 X-DashScope-Key /
 * X-FlyAI-Key / X-App-Token / X-DashScope-Plan / X-Amap-Key（后端 web_host/api/turns.py 读取
 * 页面 key 与套餐类型，web_host/app.py 中间件校验访问令牌——审计 F02）。
 * key 只存在浏览器本地，不落服务端。
 */

const DASHSCOPE_KEY = "fieldwork.dashscope_key";
const FLYAI_KEY = "fieldwork.flyai_key";
const APP_TOKEN_KEY = "fieldwork.app_token";
// 百炼套餐类型（coding/token）：套餐 Key（sk-sp-）配专属 OpenAI 兼容端点
const PLAN_KEY = "fieldwork.dashscope_plan";
// 高德 Web 服务 Key（N4 多租户）：可选项，缺省回落服务端共享 Key
const AMAP_KEY = "fieldwork.amap_key";

export interface ApiKeys {
  dashscope: string;
  flyai: string;
  appToken: string;
  plan: string;
  amap: string;
}

function read(name: string): string {
  try {
    return (localStorage.getItem(name) || "").trim();
  } catch {
    return ""; // localStorage 不可用（隐私模式等）时按无 key 处理
  }
}

export function getKeys(): ApiKeys {
  return {
    dashscope: read(DASHSCOPE_KEY),
    flyai: read(FLYAI_KEY),
    appToken: read(APP_TOKEN_KEY),
    plan: read(PLAN_KEY),
    amap: read(AMAP_KEY),
  };
}

export function hasDashScopeKey(): boolean {
  return read(DASHSCOPE_KEY) !== "";
}

/** appToken/plan/amap 传 undefined 表示"不修改已存值"（向后兼容旧调用点）。 */
export function saveKeys(dashscope: string, flyai: string, appToken?: string, plan?: string, amap?: string): void {
  const d = dashscope.trim();
  if (d) localStorage.setItem(DASHSCOPE_KEY, d);
  else localStorage.removeItem(DASHSCOPE_KEY); // 留空即移除，回落服务端配置
  const f = flyai.trim();
  if (f) localStorage.setItem(FLYAI_KEY, f);
  else localStorage.removeItem(FLYAI_KEY);
  if (appToken !== undefined) {
    const t = appToken.trim();
    if (t) localStorage.setItem(APP_TOKEN_KEY, t);
    else localStorage.removeItem(APP_TOKEN_KEY);
  }
  if (plan !== undefined) {
    const p = plan.trim();
    if (p) localStorage.setItem(PLAN_KEY, p);
    else localStorage.removeItem(PLAN_KEY); // 留空即移除，回落默认端点
  }
  if (amap !== undefined) {
    const a = amap.trim();
    if (a) localStorage.setItem(AMAP_KEY, a);
    else localStorage.removeItem(AMAP_KEY); // 留空即移除，回落服务端共享 Key
  }
}

export function clearKeys(): void {
  localStorage.removeItem(DASHSCOPE_KEY);
  localStorage.removeItem(FLYAI_KEY);
  localStorage.removeItem(APP_TOKEN_KEY);
  localStorage.removeItem(PLAN_KEY);
  localStorage.removeItem(AMAP_KEY);
}

/** 注入到 fetch headers 的鉴权头；空值不带头（key 回落服务端，令牌回落匿名）。 */
export function authHeaders(): Record<string, string> {
  const { dashscope, flyai, appToken, plan, amap } = getKeys();
  const h: Record<string, string> = {};
  if (dashscope) h["X-DashScope-Key"] = dashscope;
  if (flyai) h["X-FlyAI-Key"] = flyai;
  if (appToken) h["X-App-Token"] = appToken;
  if (plan) h["X-DashScope-Plan"] = plan;
  if (amap) h["X-Amap-Key"] = amap;
  return h;
}
