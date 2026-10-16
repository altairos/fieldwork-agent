// web/tests/rest.spec.ts —— rest 六方法请求形状 + 快照新键类型（Task 6/7 端点的前端接线）
import { afterEach, describe, expect, it, vi } from "vitest";
import { rest } from "../src/api/rest";
import type { ProjectSnapshot } from "../src/types/models";

/** stub 全局 fetch，记录 (url, init) 并按序返回假响应。 */
function stubFetch(resps: unknown[] = []) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let i = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const resp = resps[Math.min(i++, resps.length - 1)] ?? {};
    return { ok: true, status: 200, json: async () => resp } as unknown as Response;
  }));
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("rest 状态修正/确认/by-id 方法", () => {
  it("updateResearchContext 发 PUT 与 patch body", async () => {
    const calls = stubFetch([{ research_context: {}, phase_transition: null }]);
    const r = await rest.updateResearchContext("p1", { budget: "5万" });
    expect(calls[0].url).toBe("/projects/p1/research-context");
    expect(calls[0].init?.method).toBe("PUT");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ budget: "5万" });
    expect(r.phase_transition).toBeNull();
  });

  it("confirmFieldSite/confirmProposal 发 POST 到对应端点", async () => {
    const calls = stubFetch([
      { field_site: {}, phase_transition: null },
      { confirmation: {}, proposal_confirmed: true, phase_transition: null },
    ]);
    await rest.confirmFieldSite("p1", "site_1");
    expect(calls[0].url).toBe("/projects/p1/field-sites/site_1/confirm");
    expect(calls[0].init?.method).toBe("POST");
    await rest.confirmProposal("p1");
    expect(calls[1].url).toBe("/projects/p1/proposal/confirm");
    expect(calls[1].init?.method).toBe("POST");
  });

  it("adoptInformant 以 {candidate} 包装 POST", async () => {
    const calls = stubFetch([{ informant: { id: "inf_1" }, phase_transition: null }]);
    const r = await rest.adoptInformant("p1", { type: "宗族长老" });
    expect(calls[0].url).toBe("/projects/p1/informants");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ candidate: { type: "宗族长老" } });
    expect(r.informant.id).toBe("inf_1");
  });

  it("getProposalById 与下载 URL 指向 by-id 端点", async () => {
    const calls = stubFetch([{ id: "prop_1", markdown: "# x" }]);
    const r = await rest.getProposalById("p1", "prop_1");
    expect(calls[0].url).toBe("/projects/p1/proposals/prop_1");
    expect(r.id).toBe("prop_1");
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1")).toBe(
      "/projects/p1/proposals/prop_1/download");
  });
});

describe("types: 快照确认状态与记录 id", () => {
  it("ProjectSnapshot 携带 proposal_confirmed/stale 与记录 id（可选）", () => {
    const s: ProjectSnapshot = {
      project_id: "p1", phase: "finished", research_context: {},
      field_sites: [{ id: "site_1", name: "大理", confirmed: true, profile: null }],
      informants: [{ id: "inf_1", code: "INF-01", type: "长老",
                     relation: "", contact: "", notes: "" }],
      proposals: [], phase_history: [],
      proposal_confirmed: true, proposal_confirmation_stale: false,
    };
    expect(s.proposal_confirmed).toBe(true);
    expect(s.field_sites[0].id).toBe("site_1");
    expect(s.informants[0].id).toBe("inf_1");
  });
});
