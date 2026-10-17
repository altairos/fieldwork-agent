import type { CreateTurnResponse } from "../types/events";
import type {
  FieldSiteRecord, InformantRecord, ProjectSnapshot, ProjectSummary,
  ProposalDoc, ResearchContext,
} from "../types/models";
import type { ExportFormat } from "../lib/exportFormat";
import { authHeaders } from "../lib/keys";

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

/** GET /settings/status 的返回（web_host/api/settings.py，只回布尔不回明文）。 */
export interface SettingsStatus {
  dashscope_configured: boolean;
  flyai_configured: boolean;
}

/** GET /health 的返回（web_host/app.py：auth_required 表示服务端启用了 App Token）。 */
export interface HealthStatus {
  status: string;
  auth_required: boolean;
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...init?.headers },
  });
  if (!resp.ok) throw new ApiError(resp.status, await resp.text().catch(() => resp.statusText));
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
  getMessages: (id: string) =>
    req<{ role: "user" | "assistant"; content: string }[]>(`/projects/${id}/messages`),
  createTurn: (id: string, text: string, idempotencyKey?: string) =>
    req<CreateTurnResponse>(`/projects/${id}/messages`,
      { method: "POST", body: JSON.stringify({ text, idempotency_key: idempotencyKey }) }),
  cancelTurn: (id: string, turnId: string) =>
    req<{ turn_id: string; status: string }>(`/projects/${id}/turns/${turnId}/cancel`, { method: "POST" }),
  getProposal: (id: string) => req<ProposalDoc & { id: string }>(`/projects/${id}/proposal`),
  listProposals: (id: string) => req<ProposalMeta[]>(`/projects/${id}/proposals`),
  proposalDownloadUrl: (id: string, format?: ExportFormat) =>
    format && format !== "md"
      ? `/projects/${id}/proposal/download?format=${format}`
      : `/projects/${id}/proposal/download`,
  getCachedSearch: (id: string, cid: string) =>
    req<CachedSearchRecord>(`/projects/${id}/cached-searches/${cid}`),
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
