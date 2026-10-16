// web/tests/rightPanels.spec.tsx —— 右栏确认/采纳/研究信息编辑（Task 9）
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { InformantCard } from "../src/components/cards/InformantCard";
import { InformantsPanel } from "../src/components/right/InformantsPanel";
import { FieldSitesPanel } from "../src/components/right/FieldSitesPanel";
import { ProgressPanel } from "../src/components/right/ProgressPanel";
import { useProjectStore } from "../src/stores/project";
import { useToolsStore } from "../src/stores/tools";
import type { ProjectSnapshot } from "../src/types/models";

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

const SNAP: ProjectSnapshot = {
  project_id: "p1", phase: "informants", research_context: { budget: "2万" },
  field_sites: [{ id: "site_1", name: "大理", confirmed: false,
                  profile: { location: "大理" } }],
  informants: [], proposals: [], phase_history: [],
};

beforeEach(() => {
  useProjectStore.getState().reset();
  useProjectStore.getState().loadSnapshot(SNAP);
  useToolsStore.setState({ byCallId: {}, order: [] });
});
afterEach(() => vi.unstubAllGlobals());

describe("InformantCard cardActions 注入", () => {
  test("传入 cardActions 时每张候选小卡渲染操作区；不传不渲染", () => {
    const { rerender } = render(
      <InformantCard data={[{ type: "宗族长老" }]}
        cardActions={(_c, i) => <button type="button">{`采纳 ${i}`}</button>} />,
    );
    expect(screen.getByText("采纳 0")).toBeInTheDocument();
    rerender(<InformantCard data={[{ type: "宗族长老" }]} />);
    expect(screen.queryByText("采纳 0")).not.toBeInTheDocument();
  });
});

describe("InformantsPanel 采纳登记", () => {
  test("点采纳 → POST /informants + 刷新快照", async () => {
    useToolsStore.setState({
      byCallId: { c1: { call_id: "c1", tool: "suggest_informants", purpose: "", status: "done", args: {},
                        data: [{ type: "宗族长老" }], degraded: null } },
      order: ["c1"],
    });
    const calls = stubFetch([
      { informant: { id: "inf_1" }, phase_transition: null },
      { ...SNAP, informants: [{ id: "inf_1", code: "INF-01", type: "宗族长老",
                                relation: "", contact: "", notes: "" }] },
    ]);
    render(<InformantsPanel />);
    fireEvent.click(screen.getByText("采纳登记"));
    await waitFor(() => expect(calls[0].url).toBe("/projects/p1/informants"));
    expect(JSON.parse(String(calls[0].init?.body))).toEqual(
      { candidate: { type: "宗族长老" } });
    await waitFor(() =>
      expect(useProjectStore.getState().snapshot?.informants).toHaveLength(1));
  });
});

describe("FieldSitesPanel 确认", () => {
  test("未确认且带 id 的记录显示确认按钮；点击 POST 后刷新", async () => {
    const calls = stubFetch([
      { field_site: { id: "site_1", confirmed: true }, phase_transition: null },
      { ...SNAP, field_sites: [{ ...SNAP.field_sites[0], confirmed: true }] },
    ]);
    render(<FieldSitesPanel />);
    fireEvent.click(screen.getByText("确认"));
    await waitFor(() =>
      expect(calls[0].url).toBe("/projects/p1/field-sites/site_1/confirm"));
    await waitFor(() =>
      expect(useProjectStore.getState().snapshot?.field_sites[0].confirmed).toBe(true));
  });

  test("本回合 live 画像（无记录 id）不显示确认按钮", () => {
    useProjectStore.getState().loadSnapshot({ ...SNAP, field_sites: [] });
    useToolsStore.setState({
      byCallId: { c2: { call_id: "c2", tool: "profile_field_site", purpose: "", status: "done",
                        args: { location: "喜洲" }, data: { location: "喜洲" },
                        degraded: null } },
      order: ["c2"],
    });
    render(<FieldSitesPanel />);
    expect(screen.getByText("待确认")).toBeInTheDocument(); // live-only 只读 chip
    expect(screen.queryByText("确认")).not.toBeInTheDocument();
  });
});

describe("ProgressPanel 研究信息编辑", () => {
  test("改预算并保存 → PUT research-context 只带非空字段", async () => {
    const calls = stubFetch([
      { research_context: { budget: "5万" }, phase_transition: null },
      { ...SNAP, research_context: { budget: "5万" } },
    ]);
    render(<ProgressPanel />);
    fireEvent.change(screen.getByLabelText("预算"), { target: { value: "5万" } });
    fireEvent.click(screen.getByText("保存修正"));
    await waitFor(() =>
      expect(calls[0].url).toBe("/projects/p1/research-context"));
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ budget: "5万" });
    await waitFor(() =>
      expect(useProjectStore.getState().snapshot?.research_context.budget).toBe("5万"));
  });

  test("未改任何字段点保存 → 不发请求并提示", async () => {
    useProjectStore.getState().loadSnapshot({ ...SNAP, research_context: {} });
    const calls = stubFetch([]);
    render(<ProgressPanel />);
    fireEvent.click(screen.getByText("保存修正"));
    await waitFor(() =>
      expect(screen.getByText(/未填写任何要修正的字段/)).toBeInTheDocument());
    expect(calls).toHaveLength(0);
  });
});
