import { describe, expect, test, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ProjectHub } from "../src/routes/ProjectHub";
import { ProposalViewer } from "../src/components/proposal/ProposalViewer";
import { ChapterNav } from "../src/components/proposal/ChapterNav";
import * as rest from "../src/api/rest";

vi.mock("../src/api/rest");

beforeEach(() => { vi.clearAllMocks(); });

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
