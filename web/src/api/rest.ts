import type { CreateTurnResponse } from "../types/events";
import type { ProjectSnapshot, ProjectSummary, ProposalDoc } from "../types/models";
import { authHeaders } from "../lib/keys";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
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

export const rest = {
  settingsStatus: () => req<SettingsStatus>("/settings/status"),
  health: () => req<HealthStatus>("/health"),
  createProject: (body: { project_id?: string; discipline?: string; topic?: string; purpose?: string }) =>
    req<{ project_id: string; phase: string }>("/projects", { method: "POST", body: JSON.stringify(body) }),
  listProjects: () => req<ProjectSummary[]>("/projects"),
  getProject: (id: string) => req<ProjectSnapshot>(`/projects/${id}`),
  createTurn: (id: string, text: string, idempotencyKey?: string) =>
    req<CreateTurnResponse>(`/projects/${id}/messages`,
      { method: "POST", body: JSON.stringify({ text, idempotency_key: idempotencyKey }) }),
  cancelTurn: (id: string, turnId: string) =>
    req<{ turn_id: string; status: string }>(`/projects/${id}/turns/${turnId}/cancel`, { method: "POST" }),
  getProposal: (id: string) => req<ProposalDoc & { id: string }>(`/projects/${id}/proposal`),
  listProposals: (id: string) => req<ProposalMeta[]>(`/projects/${id}/proposals`),
  proposalDownloadUrl: (id: string) => `/projects/${id}/proposal/download`,
};
