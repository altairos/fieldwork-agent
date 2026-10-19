import type { CreateTurnResponse } from "../types/events";
import type {
  FieldSiteRecord, InformantRecord, ProjectSnapshot, ProjectSummary,
  ProposalDoc, ResearchContext,
} from "../types/models";
import type { ExportFormat } from "../lib/exportFormat";
import { authHeaders } from "../lib/keys";
import { ownerHeaders } from "../lib/session";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** 从 ApiError.message（后端响应文本）解析 FastAPI `{"detail": "…"}` 文案；非该形状返回 null。 */
export function apiDetail(err: ApiError): string | null {
  try {
    const parsed = JSON.parse(err.message) as { detail?: unknown };
    return typeof parsed?.detail === "string" ? parsed.detail : null;
  } catch {
    return null;
  }
}

/** App Token 守卫 401（web_host/app.py 审计 F02）：页面级 REST 的 401 唯一来源。
 *  响应体是 `{"code","message"}` 形状（非 detail）：状态码之外复核 code，
 *  区分守卫 401 与其他来源的 401；非 JSON 或无 code 键的未知形状保守放行
 *  （仍引导用户填令牌，不因形状未知而漏拦）。 */
export function isAppTokenError(err: unknown): err is ApiError {
  if (!(err instanceof ApiError) || err.status !== 401) return false;
  try {
    const parsed = JSON.parse(err.message) as { code?: unknown } | null;
    if (parsed && typeof parsed === "object" && "code" in parsed) {
      return parsed.code === "app_token_required";
    }
  } catch {
    // 非 JSON 体（网关纯文本 401）：无法复核，保守按令牌问题处理
  }
  return true;
}

/** 登录会话失效 401（web_host/api/owner.py session_expired）：与 isAppTokenError
 *  正交——该 401 体是 FastAPI HTTPException(detail={...}) 包装的
 *  `{"detail": {"code": "session_expired", …}}`，code 在 detail 内层。 */
export function isSessionExpiredError(err: unknown): err is ApiError {
  if (!(err instanceof ApiError) || err.status !== 401) return false;
  try {
    const parsed = JSON.parse(err.message) as { detail?: { code?: string } } | null;
    return parsed?.detail?.code === "session_expired";
  } catch { return false; }
}

let _onSessionExpired: (() => void) | null = null;
export function setOnSessionExpired(fn: () => void): void { _onSessionExpired = fn; }

/** GET /settings/status 的返回（web_host/api/settings.py）。
 *  JS Key 两字段是文档化例外（settings.py docstring）：高德 JS API 2.0 要求
 *  前端持有 key + securityJsCode，防滥用靠控制台域名白名单，非服务端代理可解。 */
export interface SettingsStatus {
  dashscope_configured: boolean;
  flyai_configured: boolean;
  amap_configured: boolean;
  amap_js_configured: boolean;
  amap_js_key: string;
  amap_js_security: string;
}

/** GET /health 的返回（web_host/app.py：auth_required 表示服务端启用了 App Token）。 */
export interface HealthStatus {
  status: string;
  auth_required: boolean;
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...ownerHeaders(), ...init?.headers },
  });
  if (!resp.ok) {
    const err = new ApiError(resp.status, await resp.text().catch(() => resp.statusText));
    if (isSessionExpiredError(err) && _onSessionExpired) _onSessionExpired();
    throw err;
  }
  return resp.status === 204 ? (undefined as T) : (await resp.json() as T);
}

/** 提案版本列表项（GET /projects/{id}/proposals，web_host/api/exports.py::list_proposals）。 */
export interface ProposalMeta {
  id: string;
  kind?: string;
  version?: string;
  created_at?: string;
}

/** 快照记录体（GET /projects/{id}/cached-searches/{cid}，web_host/api/projects.py，Task 10 落盘）。 */
export interface CachedSearchRecord {
  id: string;
  query: string;
  tool: string;
  cached_at?: string;
  result: { items?: { name?: string; price?: number | null }[] } | null;
}

/** 知识条目（GET /projects/{id}/knowledge，web_host/api/projects.py::list_knowledge，审计三 Fix 1b）。 */
export interface KnowledgeEntry {
  id: string;
  title: string;
  content: string;
  source?: string;
  tags?: string[];
  category?: string;
  created_at?: string;
}

/** 状态修正/确认端点共同返回的阶段推进结果；null = 本次未发生推进。 */
export interface PhaseTransition {
  from?: string;
  to?: string;
}

export const rest = {
  settingsStatus: () => req<SettingsStatus>("/settings/status"),
  health: () => req<HealthStatus>("/health"),
  createProject: (body: { project_id?: string; discipline?: string; topic?: string; purpose?: string }) =>
    req<{ project_id: string; phase: string }>("/projects", { method: "POST", body: JSON.stringify(body) }),
  listProjects: () => req<ProjectSummary[]>("/projects"),
  getProject: (id: string) => req<ProjectSnapshot>(`/projects/${id}`),
  // 删除即整域清除（后端先取消活动回合再删数据；204 无 body）
  deleteProject: (id: string) => req<void>(`/projects/${id}`, { method: "DELETE" }),
  // 元素可选携带 quick_replies（assistant 消息 meta 里的快捷作答选项，B 节；无则键缺省）
  getMessages: (id: string) =>
    req<{ role: "user" | "assistant"; content: string; quick_replies?: string[] }[]>(`/projects/${id}/messages`),
  createTurn: (id: string, text: string, idempotencyKey?: string) =>
    req<CreateTurnResponse>(`/projects/${id}/messages`,
      { method: "POST", body: JSON.stringify({ text, idempotency_key: idempotencyKey }) }),
  cancelTurn: (id: string, turnId: string) =>
    req<{ turn_id: string; status: string }>(`/projects/${id}/turns/${turnId}/cancel`, { method: "POST" }),
  // 重新生成：后端弹出末条 assistant + user，以同一文本新建回合（201）
  retryTurn: (id: string, turnId: string) =>
    req<CreateTurnResponse>(`/projects/${id}/turns/${turnId}/retry`, { method: "POST" }),
  getProposal: (id: string) => req<ProposalDoc & { id: string }>(`/projects/${id}/proposal`),
  listProposals: (id: string) => req<ProposalMeta[]>(`/projects/${id}/proposals`),
  proposalDownloadUrl: (id: string, format?: ExportFormat) =>
    format && format !== "md"
      ? `/projects/${id}/proposal/download?format=${format}`
      : `/projects/${id}/proposal/download`,
  getCachedSearch: (id: string, cid: string) =>
    req<CachedSearchRecord>(`/projects/${id}/cached-searches/${cid}`),
  // 审计三 Fix 1c：agent add_knowledge_entry 落盘条目的可见面（按创建顺序，含正文）
  listKnowledge: (id: string) => req<KnowledgeEntry[]>(`/projects/${id}/knowledge`),
  // P1 右栏编辑：改 title/content（未提供的字段不改），返回更新后条目
  updateKnowledge: (id: string, entryId: string, patch: { title?: string; content?: string }) =>
    req<KnowledgeEntry>(`/projects/${id}/knowledge/${entryId}`, { method: "PUT", body: JSON.stringify(patch) }),
  // ---- 状态修正与确认（web_host/api/state.py，审计 F03/F04 修正入口）----
  updateResearchContext: (id: string, patch: Record<string, string | boolean>) =>
    req<{ research_context: Partial<ResearchContext>; phase_transition: PhaseTransition | null }>(
      `/projects/${id}/research-context`, { method: "PUT", body: JSON.stringify(patch) }),
  confirmFieldSite: (id: string, siteId: string) =>
    req<{ field_site: FieldSiteRecord; phase_transition: PhaseTransition | null }>(
      `/projects/${id}/field-sites/${siteId}/confirm`, { method: "POST" }),
  adoptInformant: (id: string, candidate: Record<string, unknown>) =>
    req<{ informant: InformantRecord & { id: string }; phase_transition: PhaseTransition | null }>(
      `/projects/${id}/informants`, { method: "POST", body: JSON.stringify({ candidate }) }),
  // 审计二 R09：确认必须指定文档（后端四重校验，404/400/409 拒绝）
  confirmProposal: (id: string, docId: string) =>
    req<{ confirmation: Record<string, unknown>; proposal_confirmed: boolean;
          phase_transition: PhaseTransition | null }>(
      `/projects/${id}/proposal/confirm`,
      { method: "POST", body: JSON.stringify({ doc_id: docId }) }),
  // ---- 按版本读取/下载（web_host/api/exports.py by-id 端点，审计 F13）----
  getProposalById: (id: string, docId: string) =>
    req<ProposalDoc & { id: string }>(`/projects/${id}/proposals/${docId}`),
  proposalByIdDownloadUrl: (id: string, docId: string, format?: ExportFormat) =>
    format && format !== "md"
      ? `/projects/${id}/proposals/${docId}/download?format=${format}`
      : `/projects/${id}/proposals/${docId}/download`,
};
