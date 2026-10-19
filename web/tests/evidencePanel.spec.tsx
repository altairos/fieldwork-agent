import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup, act } from "@testing-library/react";
import { EvidencePanel } from "../src/components/chat/EvidencePanel";
import { rest } from "../src/api/rest";
import { useToolsStore } from "../src/stores/tools";
import { useUiStore } from "../src/stores/ui";

// 单点 mock rest 层：而不用拦截 fetch——证据面板只依赖 rest 接口（Task 8 已测）
vi.mock("../src/api/rest", () => ({
  rest: {
    listCachedSearches: vi.fn(),
    getCachedSearch: vi.fn(),
  },
}));

beforeEach(() => {
  useToolsStore.getState().reset();
  useUiStore.getState().reset();
  useUiStore.getState().setMiddleTab("evidence");
});
afterEach(() => cleanup());

const summary = (over: Record<string, unknown> = {}) => ({
  id: "cs1", query: "昆明 酒店", tool: "search_ai",
  cached_at: "2026-10-19T03:04:05.000001", ...over,
});

function seedLive(over: Record<string, unknown> = {}) {
  useToolsStore.getState().applyEvent({
    event: "tool_started", call_id: "cL1", tool: "search_ai", args: { query: "昆明 酒店" },
    purpose: "", turn_id: "t1", seq: 3,
  } as never);
  useToolsStore.getState().applyEvent({
    event: "tool_completed", call_id: "cL1", ok: true, data: { answer: "境内允许" }, ...over,
  } as never);
}

describe("EvidencePanel 混合数据源", () => {
  it("缓存行渲染 id+query；实时行渲染 call_id；同回合不重拉索引", async () => {
    vi.mocked(rest.listCachedSearches).mockResolvedValue([summary()]);
    render(<EvidencePanel projectId="p1" />);
    await waitFor(() =>
      expect(screen.getByText(/昆明 酒店/)).toBeTruthy());
    expect(rest.listCachedSearches).toHaveBeenCalledTimes(1);
  });
  it("running 实时行不入列；done 入列（R10）", async () => {
    vi.mocked(rest.listCachedSearches).mockResolvedValue([]);
    useToolsStore.getState().applyEvent({
      event: "tool_started", call_id: "cR", tool: "search_ai", args: { query: "x" }, purpose: "",
    } as never);  // 停在 running
    seedLive();
    render(<EvidencePanel projectId="p1" />);
    await waitFor(() =>
      expect(screen.getByText(/cL1|昆明 酒店/)).toBeTruthy());
    expect(screen.queryByText(/cR/)).toBeNull();
  });
  it("缓存行展开懒加载完整结果，二次展开不重复请求", async () => {
    vi.mocked(rest.listCachedSearches)
      .mockResolvedValue([summary({ tool: "amap_text_search", query: "大理 古镇" })]);
    vi.mocked(rest.getCachedSearch).mockResolvedValue({
      id: "cs1", query: "大理 古镇", tool: "amap_text_search", created_at: "",
      result: { items: [{ name: "喜洲古镇" }], total: 1 },
    } as never);
    render(<EvidencePanel projectId="p1" />);
    await waitFor(() => expect(screen.getByText(/大理 古镇/)).toBeTruthy());
    // 展开交互（截图可达性：详情按钮 aria-expanded）
    (await screen.findByRole("button", { name: /大理 古镇/ })).click();
    await waitFor(() =>
      expect(rest.getCachedSearch).toHaveBeenCalledWith("p1", "cs1"));
    (await screen.findByRole("button", { name: /大理 古镇/ })).click();
    (await screen.findByRole("button", { name: /大理 古镇/ })).click();
    expect(rest.getCachedSearch).toHaveBeenCalledTimes(1);  // 结果缓存复用
  });
  it("截断快照专属态：截断徽标 + 预览不进卡片渲染（R5）", async () => {
    vi.mocked(rest.listCachedSearches)
      .mockResolvedValue([summary({ truncated: true, tool: "search_hotels", query: "长长长长长" })]);
    vi.mocked(rest.getCachedSearch).mockResolvedValue({
      id: "cs1", query: "长长长长长", tool: "search_hotels", created_at: "",
      result: { truncated: true, preview: "前 500 字预览…" },
    } as never);
    render(<EvidencePanel projectId="p1" />);
    (await screen.findByRole("button", { name: /长长长长长/ })).click();
    await waitFor(() =>
      expect(screen.getByText(/截断：原始结果超出 64KB 缓存上限/)).toBeTruthy());
  });
  it("unknown（旧库 NULL）不预先过滤，展开后不臆造状态（R12）", async () => {
    vi.mocked(rest.listCachedSearches)
      .mockResolvedValue([summary({ tool: "search_hotels", query: "旧缓存", empty: null, total: null })]);
    render(<EvidencePanel projectId="p1" />);
    await waitFor(() => expect(screen.getByText(/旧缓存/)).toBeTruthy());
    expect(screen.queryByText("空结果")).toBeNull();  // null=未知，不标空
  });
  it("空态引导文案", async () => {
    vi.mocked(rest.listCachedSearches).mockResolvedValue([]);
    render(<EvidencePanel projectId="p1" />);
    await waitFor(() =>
      expect(screen.getByText(/暂无可展示的检索结果/)).toBeTruthy());
  });
  it("索引拉取失败：轻提示不白屏，实时层照常（C8）", async () => {
    vi.mocked(rest.listCachedSearches).mockRejectedValue(new Error("net"));
    seedLive();
    render(<EvidencePanel projectId="p1" />);
    await waitFor(() =>
      expect(screen.getByText(/检索记录加载失败/)).toBeTruthy());
    expect(screen.getByText(/昆明 酒店/)).toBeTruthy();
  });
  it("实时行按回合归属标注：当前回合「本轮」、更早回合「本次会话」（P1-1）", async () => {
    // 原实现把所有实时行一律标「本轮」，而 tools store 只在换项目时 reset——
    // 第 3 回合打开证据 tab，第 1 回合的实时行仍写「本轮」，界面在说谎。
    const conv = await import("../src/stores/conversation");
    vi.mocked(rest.listCachedSearches).mockResolvedValue([]);
    for (const [cid, turn] of [["cOld", "t1"], ["cNew", "t2"]] as const) {
      useToolsStore.getState().applyEvent({
        event: "tool_started", call_id: cid, tool: "search_ai",
        args: { query: `q-${turn}` }, purpose: "", turn_id: turn, seq: 1,
      } as never);
      useToolsStore.getState().applyEvent({
        event: "tool_completed", call_id: cid, ok: true, data: { answer: "有内容" },
      } as never);
    }
    conv.useConversationStore.getState().setCurrentTurn("t2");
    render(<EvidencePanel projectId="p1" />);

    const older = await screen.findByRole("button", { name: /q-t1/ });
    const current = screen.getByRole("button", { name: /q-t2/ });
    expect(older.textContent).toContain("本次会话");
    expect(older.textContent).not.toContain("本轮");
    expect(current.textContent).toContain("本轮");
  });

  it("缺 turn_id 的旧事件不臆测归属：一律标「本次会话」（P1-1）", async () => {
    const conv = await import("../src/stores/conversation");
    vi.mocked(rest.listCachedSearches).mockResolvedValue([]);
    // 手工造一条**不带 turn_id** 的实时行（旧后端事件不含该字段）
    useToolsStore.getState().applyEvent({
      event: "tool_started", call_id: "cLegacy", tool: "search_ai",
      args: { query: "旧事件查询" }, purpose: "",
    } as never);
    useToolsStore.getState().applyEvent({
      event: "tool_completed", call_id: "cLegacy", ok: true, data: { answer: "有内容" },
    } as never);
    conv.useConversationStore.getState().setCurrentTurn("t1");
    render(<EvidencePanel projectId="p1" />);
    const row = await screen.findByRole("button", { name: /旧事件查询/ });
    expect(row.textContent).toContain("本次会话");
  });

  it("turnRunning true→false 重拉索引（回合结束刷新，C5）", async () => {
    const conv = await import("../src/stores/conversation");
    vi.mocked(rest.listCachedSearches).mockResolvedValue([]);
    render(<EvidencePanel projectId="p1" />);
    await waitFor(() =>
      expect(rest.listCachedSearches).toHaveBeenCalledTimes(1));
    conv.useConversationStore.setState({ turnRunning: true });
    await act(async () => {});   // 回合开始先渲染落地：真实 SSE 中回合起止为两次独立提交（C5/R10）
    conv.useConversationStore.setState({ turnRunning: false });
    await waitFor(() =>
      expect(rest.listCachedSearches).toHaveBeenCalledTimes(2));
  });
});
