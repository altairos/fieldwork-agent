import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProposalView } from "../src/routes/ProposalView";

const LATEST = {
  id: "prop_2",
  kind: "proposal",
  version: "final",
  created_at: "2026-10-16T10:00:00",
  markdown: "# 最新版正文\n\n段落。",
  // 真实后端 _proposal_payload 的 sections 仅从 `## ` 行派生；h1-only 正文 → []
  sections: [],
};
const V1 = {
  id: "prop_1",
  kind: "proposal",
  version: "draft",
  created_at: "2026-10-15T10:00:00",
  markdown: "# 初版正文\n\n段落。",
  sections: [],
};
const GUIDE = {
  id: "guide_1",
  kind: "interview_guide",
  version: "draft",
  created_at: "2026-10-16T11:00:00",
  markdown: "# 提纲正文\n\n段落。",
  sections: [],
};
const VERSIONS = [V1, LATEST, GUIDE];
const SNAP = {
  id: "p1",
  phase: "proposal_generation",
  research_context: { topic: "宗族变迁" },
  field_sites: [],
  informants: [],
};

function json(payload: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => String(payload),
  } as Response;
}

/** 按 URL 分发的 fetch stub；返回调用记录（url + init）。 */
function stubFetch(snap: object = SNAP) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/projects/p1") return json(snap);
      if (url === "/projects/p1/proposals") return json(VERSIONS);
      if (url === "/projects/p1/proposals/prop_1") return json(V1);
      if (url === "/projects/p1/proposals/guide_1") return json(GUIDE);
      if (url === "/projects/p1/proposal") return json(LATEST);
      if (url === "/projects/p1/proposal/confirm") {
        return json({
          confirmation: {},
          proposal_confirmed: true,
          phase_transition: { from: "proposal_generation", to: "finished" },
        });
      }
      return json({});
    }),
  );
  return calls;
}

/** stubFetch 变体：confirm 端点返回 4xx + {detail}（R09 拒绝路径），其余端点照常。 */
function stubFetchWithConfirmFailure(failure: { status: number; detail: string }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, _init?: RequestInit) => {
      if (url === "/projects/p1/proposal/confirm") {
        return {
          ok: false,
          status: failure.status,
          text: async () => JSON.stringify({ detail: failure.detail }),
        } as Response;
      }
      if (url === "/projects/p1") return json(SNAP);
      if (url === "/projects/p1/proposals") return json(VERSIONS);
      if (url === "/projects/p1/proposal") return json(LATEST);
      return json({});
    }),
  );
}

function mount() {
  return render(
    <MemoryRouter initialEntries={["/p/p1/proposal"]}>
      <Routes>
        <Route path="/p/:id/proposal" element={<ProposalView />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("ProposalView 版本/kind 加载与方案确认", () => {
  it("初载渲染最新版正文与 final chip（回归保护，现状即绿）", async () => {
    const calls = stubFetch();
    mount();
    expect(await screen.findByText("最新版正文")).toBeTruthy();
    expect(screen.getByText("final")).toBeTruthy();
    const urls = calls.map((c) => c.url);
    expect(urls).toContain("/projects/p1/proposal");
    expect(urls).toContain("/projects/p1/proposals");
  });

  it("点版本 chip 后按 id 加载对应正文（F13）", async () => {
    const calls = stubFetch();
    mount();
    await screen.findByText("最新版正文");
    // VERSIONS 顺序 [V1, LATEST, GUIDE]：DOM 中第一枚「draft v1」即 prop_1
    fireEvent.click(screen.getAllByText("draft v1")[0]);
    expect(await screen.findByText("初版正文")).toBeTruthy();
    expect(calls.some((c) => c.url === "/projects/p1/proposals/prop_1")).toBe(true);
  });

  it("点 kind 切换按钮加载访谈提纲正文（F13）", async () => {
    stubFetch();
    mount();
    await screen.findByText("最新版正文");
    fireEvent.click(screen.getByRole("button", { name: /访谈提纲/ }));
    expect(await screen.findByText("提纲正文")).toBeTruthy();
  });

  it("点确认方案——POST confirm 携 doc_id、回读快照、提示阶段推进（F04/R09）", async () => {
    const calls = stubFetch();
    mount();
    await screen.findByText("最新版正文");
    fireEvent.click(screen.getByRole("button", { name: "确认方案" }));
    await waitFor(() => screen.getByText(/阶段推进至 finished/));
    const confirms = calls.filter((c) => c.url === "/projects/p1/proposal/confirm");
    expect(confirms).toHaveLength(1);
    expect((confirms[0].init as RequestInit | undefined)?.method).toBe("POST");
    expect(JSON.parse(String((confirms[0].init as RequestInit).body)))
      .toEqual({ doc_id: "prop_2" });
    // confirm 后需回读项目快照刷新题头/阶段/确认状态
    expect(calls.filter((c) => c.url === "/projects/p1").length).toBeGreaterThanOrEqual(2);
  });

  it("stale 快照——提示重新确认且按钮文案变化（F04）", async () => {
    stubFetch({ ...SNAP, proposal_confirmed: true, proposal_confirmation_stale: true });
    mount();
    expect(await screen.findByText(/请重新确认/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新确认方案" })).toBeTruthy();
  });

  it("kind 切换到访谈提纲后确认按钮禁用（R09：只能确认 proposal）", async () => {
    stubFetch();
    mount();
    await screen.findByText("最新版正文");
    // 「访谈提纲（kind 切换）」按钮 → toggleKind 从版本列表取 guide_1（by-id 加载）
    fireEvent.click(screen.getByRole("button", { name: /访谈提纲/ }));
    expect(await screen.findByText("提纲正文")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认方案" })).toBeDisabled();
  });

  it("确认被拒（409）——直显后端 detail 文案（R09）", async () => {
    stubFetchWithConfirmFailure({
      status: 409,
      detail: "研究输入已变化，该文档的确认已过期：请重新生成方案",
    });
    mount();
    await screen.findByText("最新版正文");
    fireEvent.click(screen.getByRole("button", { name: "确认方案" }));
    expect(await screen.findByText(/请重新生成方案/)).toBeTruthy();
  });
});
