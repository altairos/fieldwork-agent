import { describe, expect, test, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ProjectHub } from "../src/routes/ProjectHub";
import { ProposalView } from "../src/routes/ProposalView";
import { ProposalViewer } from "../src/components/proposal/ProposalViewer";
import { ChapterNav } from "../src/components/proposal/ChapterNav";
import { saveKeys } from "../src/lib/keys";
import * as rest from "../src/api/rest";

vi.mock("../src/api/rest");

beforeEach(() => {
  vi.clearAllMocks();
  // ApiKeyCard 挂载即查 key 状态与健康探询：模块 mock 默认返 undefined 会崩 .then，补默认值
  vi.mocked(rest.rest.settingsStatus).mockResolvedValue({ dashscope_configured: true, flyai_configured: true });
  vi.mocked(rest.rest.health).mockResolvedValue({ status: "ok", auth_required: false });
});

describe("ProjectHub", () => {
  test("渲染项目列表", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([
      { project_id: "p1", phase: "itinerary_planning", research_context: { discipline: "社会学", topic: "宗族变迁" }, counts: { sites: 2, informants: 4, proposals: 1 } },
    ] as any);
    render(<MemoryRouter><ProjectHub /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/宗族变迁|p1/)).toBeInTheDocument());
  });
  test("空列表显示空状态", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([]);
    render(<MemoryRouter><ProjectHub /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/还没有|从一个想法|空/i)).toBeInTheDocument());
  });
});

describe("ProposalViewer", () => {
  test("渲染 markdown 章节", () => {
    render(<ProposalViewer markdown={"# 选题依据\n\n本研究…\n\n## 方法论"} kind="proposal" version="final" />);
    expect(screen.getByText(/选题依据/)).toBeInTheDocument();
    expect(screen.getByText(/本研究/)).toBeInTheDocument();
  });
});

describe("ChapterNav", () => {
  test("八章导航高亮当前", () => {
    render(<ChapterNav sections={["一 · 选题依据","二 · 田野点","三 · 研究设计"]} activeIndex={2} onSelect={()=>{}} />);
    expect(screen.getByText(/研究设计/)).toBeInTheDocument();
  });
});

// ──────────────────────────────────────────────────────────────────────────
// 终审#1：提案下载必须走 fetch + authHeaders()（/projects 前缀受 F02 令牌保护，
// 顶层锚点导航带不了自定义头，启用 App Token 时必 401）。
// ──────────────────────────────────────────────────────────────────────────
describe("ProposalView 下载（终审#1）", () => {
  const doc = {
    id: "doc1", kind: "proposal", version: "draft",
    markdown: "# 一、选题依据\n\n正文", sections: ["一、选题依据"],
    requested_mode: "", mode: "", inputs_hash: "",
  };
  const createObjectURL = vi.fn(() => "blob:proposal-md");
  const revokeObjectURL = vi.fn();
  let calls: Array<[string, RequestInit | undefined]> = [];

  beforeEach(() => {
    localStorage.clear();
    saveKeys("", "", "tok-9"); // authHeaders() 必须在点击时读取：新存令牌即时生效
    calls = [];
    // jsdom 未实现 createObjectURL/revokeObjectURL，注入可断言替身
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true, writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true, writable: true });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return { ok: true, blob: async () => new Blob(["# 研究方案"]) } as unknown as Response;
    }));
    vi.mocked(rest.rest.getProposal).mockResolvedValue(doc as any);
    vi.mocked(rest.rest.listProposals).mockResolvedValue([{ id: "doc1", kind: "proposal", version: "draft" }]);
    vi.mocked(rest.rest.getProject).mockResolvedValue({ research_context: { topic: "宗族变迁" }, phase: "research_positioning" } as any);
    vi.mocked(rest.rest.proposalDownloadUrl).mockImplementation((id: string) => `/projects/${id}/proposal/download`);
    vi.mocked(rest.rest.proposalByIdDownloadUrl).mockImplementation(
      (id: string, docId: string) => `/projects/${id}/proposals/${docId}/download`,
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  function renderView() {
    return render(
      <MemoryRouter initialEntries={["/p/p9/proposal"]}>
        <Routes>
          <Route path="/p/:id/proposal" element={<ProposalView />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  test("下载走 fetch 并注入 X-App-Token，blob → objectURL 临时链", async () => {
    renderView();
    const btn = await screen.findByRole("button", { name: /下载 \.md/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(calls[0][0]).toBe("/projects/p9/proposals/doc1/download");
    expect(calls[0][1]?.headers).toMatchObject({ "X-App-Token": "tok-9" });
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:proposal-md");
  });

  test("失败（如 401）显示下载失败提示，不触发 objectURL", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 401 } as unknown as Response)));
    renderView();
    const btn = await screen.findByRole("button", { name: /下载 \.md/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByText(/下载失败/)).toBeInTheDocument());
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
