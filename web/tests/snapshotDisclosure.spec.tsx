import { afterEach, describe, expect, test, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SnapshotDisclosure, snapshotCid } from "../src/components/cards/SnapshotDisclosure";
import { BudgetCard } from "../src/components/cards/BudgetCard";
import { useProjectStore } from "../src/stores/project";

// Task 11 约定格式的 source_ref + Task 10 端点返回体（手算样例）
const REF = "民宿（flyai 快照 srch_abc123（2026-10-16 查询））";
const RECORD = {
  id: "srch_abc123",
  query: 'search_hotels {"dest_name": "大理"}',
  tool: "search_hotels",
  cached_at: "2026-10-16T08:00:00",
  result: { items: [{ name: "大理客栈", price: 180 }, { name: "洱海民宿", price: 220 }] },
};

function stubFetch(ok: boolean, body: unknown) {
  const calls: Array<[string, RequestInit | undefined]> = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push([url, init]);
    return (ok
      ? { ok: true, status: 200, json: async () => body }
      : { ok: false, status: 404, statusText: "Not Found", text: async () => "gone" }
    ) as unknown as Response;
  });
  return { fn, calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
  useProjectStore.setState({ projectId: null });
});

describe("snapshotCid 提取", () => {
  test("从 source_ref 提取 cid；无引用返回 null", () => {
    expect(snapshotCid(REF)).toBe("srch_abc123");
    expect(snapshotCid("MU5843")).toBeNull();
    expect(snapshotCid("G29（flyai 实时报价）")).toBeNull();
  });
});

describe("SnapshotDisclosure 懒加载披露", () => {
  test("渲染不发请求；点击后拉快照并展示条目", async () => {
    useProjectStore.setState({ projectId: "p1" });
    const { fn, calls } = stubFetch(true, RECORD);
    vi.stubGlobal("fetch", fn);
    render(<SnapshotDisclosure sourceRef={REF} />);
    expect(fn).not.toHaveBeenCalled(); // 懒加载：披露才请求
    fireEvent.click(screen.getByRole("button", { name: "查看报价快照证据" }));
    expect(await screen.findByText("· 大理客栈 ¥180")).toBeInTheDocument();
    expect(calls[0][0]).toBe("/projects/p1/cached-searches/srch_abc123");
  });

  test("无 cid 或无 projectId 时整体不渲染", () => {
    useProjectStore.setState({ projectId: "p1" });
    const first = render(<SnapshotDisclosure sourceRef="MU5843" />);
    expect(first.container.innerHTML).toBe("");
    useProjectStore.setState({ projectId: null });
    const again = render(<SnapshotDisclosure sourceRef={REF} />);
    expect(again.container.innerHTML).toBe("");
  });

  test("404 → 错误文案，不抛错（红线：卡片永不抛错）", async () => {
    useProjectStore.setState({ projectId: "p1" });
    const { fn } = stubFetch(false, null);
    vi.stubGlobal("fetch", fn);
    render(<SnapshotDisclosure sourceRef="民宿（flyai 快照 srch_ghost（2026-10-16 查询））" />);
    fireEvent.click(screen.getByRole("button", { name: "查看报价快照证据" }));
    expect(await screen.findByText(/快照不可用/)).toBeInTheDocument();
  });

  test("BudgetCard 明细内接线（集成）", async () => {
    useProjectStore.setState({ projectId: "p1" });
    const { fn } = stubFetch(true, RECORD);
    vi.stubGlobal("fetch", fn);
    render(
      <BudgetCard
        data={{
          items: [{ name: "住宿（大理）", amount: 4500, source: "cheapest", currency: "CNY",
            unit: "每晚价×夜数×房间数", quantity: 30, people: 1, priced_at: "",
            source_ref: REF }],
          total: 4500, budget_total: 5000, over_budget: false, coverage: "complete", uncovered: [],
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "查看报价快照证据" }));
    expect(await screen.findByText("· 大理客栈 ¥180")).toBeInTheDocument();
  });
});
