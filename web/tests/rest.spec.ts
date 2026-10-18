// web/tests/rest.spec.ts —— rest 六方法请求形状 + 快照新键类型（Task 6/7 端点的前端接线）
import type { Server } from "node:http";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError, apiDetail, isAppTokenError, rest } from "../src/api/rest";
import { startMockServer } from "../mock/sseServer";
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

  it("updateKnowledge 发 PUT 到条目端点并返回更新后条目（P1 右栏编辑）", async () => {
    const calls = stubFetch([{ id: "k1", title: "新题", content: "新文\n第二行" }]);
    const r = await rest.updateKnowledge("p1", "k1", { title: "新题", content: "新文\n第二行" });
    expect(calls[0].url).toBe("/projects/p1/knowledge/k1");
    expect(calls[0].init?.method).toBe("PUT");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ title: "新题", content: "新文\n第二行" });
    expect(r.title).toBe("新题");
  });

  it("confirmFieldSite/confirmProposal 发 POST 到对应端点（R09：确认携 doc_id）", async () => {
    const calls = stubFetch([
      { field_site: {}, phase_transition: null },
      { confirmation: {}, proposal_confirmed: true, phase_transition: null },
    ]);
    await rest.confirmFieldSite("p1", "site_1");
    expect(calls[0].url).toBe("/projects/p1/field-sites/site_1/confirm");
    expect(calls[0].init?.method).toBe("POST");
    await rest.confirmProposal("p1", "prop_2");
    expect(calls[1].url).toBe("/projects/p1/proposal/confirm");
    expect(calls[1].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ doc_id: "prop_2" });
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

  it("apiDetail 解析 FastAPI {detail} 文案（R09）", () => {
    expect(apiDetail(new ApiError(409,
      JSON.stringify({ detail: "研究输入已变化：请重新生成方案" }))))
      .toBe("研究输入已变化：请重新生成方案");
    expect(apiDetail(new ApiError(500, "Internal Error"))).toBeNull();
  });

  it("getMessages 发 GET /projects/{id}/messages（刷新回填）", async () => {
    // stubFetch 的 resps[0] 即 json() 返回体：本端点返回消息数组，需双层嵌套
    const calls = stubFetch([[{ role: "user", content: "你好" }]]);
    const r = await rest.getMessages("p1");
    expect(calls[0].url).toBe("/projects/p1/messages");
    expect(r).toEqual([{ role: "user", content: "你好" }]);
  });
});

describe("isAppTokenError — 401 后 code 复核（后续小任务 a）", () => {
  const tokenBody = JSON.stringify({
    code: "app_token_required",
    message: "缺少或错误的 X-App-Token 请求头：请在首页「API 密钥」卡填写访问令牌",
  });

  it("401 + 响应体 code=app_token_required → true", () => {
    expect(isAppTokenError(new ApiError(401, tokenBody))).toBe(true);
  });

  it("401 + JSON 体但 code 非 app_token_required → false（其他来源 401 不误判）", () => {
    expect(isAppTokenError(new ApiError(401,
      JSON.stringify({ code: "upstream_auth", message: "上游拒绝" })))).toBe(false);
  });

  it("401 + 非 JSON 体（网关纯文本）→ 保守放行 true（仍引导用户填令牌）", () => {
    expect(isAppTokenError(new ApiError(401, "Unauthorized"))).toBe(true);
  });

  it("非 401 或非 ApiError → false（回归）", () => {
    expect(isAppTokenError(new ApiError(403, tokenBody))).toBe(false);
    expect(isAppTokenError(new Error("网络中断"))).toBe(false);
    expect(isAppTokenError(null)).toBe(false);
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

describe("rest.proposalByIdDownloadUrl", () => {
  it("双参：不追加 ?format=（零破坏）", () => {
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1")).toBe(
      "/projects/p1/proposals/prop_1/download"
    );
  });

  it("显式 md：与双参相同", () => {
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1", "md")).toBe(
      "/projects/p1/proposals/prop_1/download"
    );
  });

  it("docx：追加 ?format=docx", () => {
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1", "docx")).toBe(
      "/projects/p1/proposals/prop_1/download?format=docx"
    );
  });

  it("txt：追加 ?format=txt", () => {
    expect(rest.proposalByIdDownloadUrl("p1", "prop_1", "txt")).toBe(
      "/projects/p1/proposals/prop_1/download?format=txt"
    );
  });
});

describe("rest.proposalDownloadUrl", () => {
  it("无参：不追加 ?format=（零破坏）", () => {
    expect(rest.proposalDownloadUrl("p1")).toBe("/projects/p1/proposal/download");
  });

  it("显式 md：与无参相同", () => {
    expect(rest.proposalDownloadUrl("p1", "md")).toBe(
      "/projects/p1/proposal/download"
    );
  });

  it("docx：追加 ?format=docx", () => {
    expect(rest.proposalDownloadUrl("p1", "docx")).toBe(
      "/projects/p1/proposal/download?format=docx"
    );
  });

  it("txt：追加 ?format=txt", () => {
    expect(rest.proposalDownloadUrl("p1", "txt")).toBe(
      "/projects/p1/proposal/download?format=txt"
    );
  });
});

describe("mock 服务器 download 三格式", () => {
  let server: Server;

  beforeAll(async () => {
    server = await startMockServer(0, 5);
  });

  afterAll(() => {
    server.close();
  });

  function base(): string {
    const addr = server.address();
    if (addr === null || typeof addr === "string") {
      throw new Error("临时端口未就绪");
    }
    return `http://127.0.0.1:${addr.port}`;
  }

  it("默认 md：text/markdown + proposal.md + 正文含 #", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposal/download`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/markdown");
    expect(res.headers.get("content-disposition")).toContain('proposal.md');
    const text = await res.text();
    expect(text).toContain("# ");
  });

  it("txt：text/plain + proposal.txt + 正文无 #", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposal/download?format=txt`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/plain");
    expect(res.headers.get("content-disposition")).toContain('proposal.txt');
    const text = await res.text();
    expect(text).not.toContain("# ");
    expect(text).toContain("白族村落宗族组织变迁研究");
  });

  it("docx：wordprocessingml + proposal.docx + PK 魔数", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposal/download?format=docx`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    );
    expect(res.headers.get("content-disposition")).toContain('proposal.docx');
    const buf = Buffer.from(await res.arrayBuffer());
    expect(buf.subarray(0, 4).toString("latin1")).toBe("PK\u0003\u0004");
  });

  it("by-id 默认 md：text/markdown + attachment", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposals/doc_1/download`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/markdown");
    expect(res.headers.get("content-disposition")).toContain("attachment");
    const text = await res.text();
    expect(text).toContain("# ");
  });

  it("by-id docx：wordprocessingml + PK 魔数", async () => {
    const res = await fetch(`${base()}/projects/pc1/proposals/doc_1/download?format=docx`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    );
    expect(res.headers.get("content-disposition")).toContain(".docx");
    const buf = Buffer.from(await res.arrayBuffer());
    expect(buf.subarray(0, 4).toString("latin1")).toBe("PK\u0003\u0004");
  });
});
