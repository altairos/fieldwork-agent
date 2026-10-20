import { describe, expect, it, vi } from "vitest";
import { mergeEvidence } from "../src/lib/evidenceFilter";
import type { CachedSearchSummary } from "../src/api/rest";
import type { ToolCall } from "../src/stores/tools";

// C7-1 可达性：真实 registry 的 collapsed 工具在 Task 9 后全有专用卡，
// 文件级 mock 让 amap_text_search 落 Fallback 构造该分支输入（本文件无其他用例依赖它）
vi.mock("../src/components/cards/registry", async (importOriginal) => {
  const mod = await importOriginal<
    typeof import("../src/components/cards/registry")
  >();
  return {
    ...mod,
    getToolCard: (tool: string) =>
      tool === "amap_text_search" ? mod.getToolCard("__no_card__") : mod.getToolCard(tool),
  };
});

const cache = (over: Partial<CachedSearchSummary>): CachedSearchSummary => ({
  id: "cs1", query: "昆明 酒店", tool: "search_ai",
  cached_at: "2026-10-19T03:04:05.000001", ...over,
});
const live = (over: Partial<ToolCall>): ToolCall => ({
  call_id: "c1", tool: "search_ai", args: { query: "昆明 酒店" },
  purpose: "", status: "done", ...over,
});

describe("mergeEvidence 跨层配对（R13）", () => {
  it("call_id==parent_call_id 且 tool 相同 → 实时行合入缓存行，不重复显示", () => {
    const rows = mergeEvidence(
      [cache({ id: "csA", parent_call_id: "cX", query_key: "search_ai|{\"query\":\"昆明 酒店\"}" })],
      [live({ call_id: "cX", tool: "search_ai" })],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].origin).toBe("cache");
    expect(rows[0].callId).toBe("cX");
  });
  it("同(parent,tool)多快照不互配（聚合内部双策略快照，R13 反例）", () => {
    const rows = mergeEvidence(
      [cache({ id: "csA", parent_call_id: "cP", tool: "search_hotels" }),
       cache({ id: "csB", parent_call_id: "cP", tool: "search_hotels" })],
      [],
    );
    expect(rows).toHaveLength(2);  // 两份都保留，配对只发生在 实时↔缓存 之间
  });
  it("running 状态的实时行不入列（准入限制）", () => {
    const rows = mergeEvidence([], [live({ status: "running" })]);
    expect(rows).toHaveLength(0);
  });
  it("failed 实时行不入列（C7-3：失败由对话 tab 活动条承担，证据 tab 不重复）", () => {
    const rows = mergeEvidence([], [live({ status: "failed", ok: false })]);
    expect(rows).toHaveLength(0);
  });
  it("实时失败但缓存成功 → 以缓存行为准，不产生额外占位（R4：成功不被失败遮掉）", () => {
    const rows = mergeEvidence(
      [cache({ id: "csA", parent_call_id: "cF", query_key: "" })],
      [live({ call_id: "cF", status: "failed", ok: false })],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].origin).toBe("cache");
  });
});

describe("mergeEvidence 去重与时序（R11/R15）", () => {
  it("query_key 相同的两条缓存行只留最新一条（索引序=时间序，C02）", () => {
    const k = "search_ai|{\"query\":\"大理\"}";
    const rows = mergeEvidence(
      [cache({ id: "cs1", query: "大理", query_key: k, cached_at: "2026-10-19T01:00:00.000001" }),
       cache({ id: "cs2", query: "大理", query_key: k, cached_at: "2026-10-19T02:00:00.000001" })],
      [],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].key).toBe("cs2");   // 刷新后新结果可见（旧实现留 cs1，新结果被折叠消失）
  });
  it("缓存行保持传入索引序（时间序由后端 ORDER BY 保证，R11）", () => {
    const rows = mergeEvidence(
      [cache({ id: "early", cached_at: "2026-10-19T09:00:00.000002" }),
       cache({ id: "late", cached_at: "2026-10-19T09:00:00.000009" })],
      [],
    );
    expect(rows.map((r) => r.key)).toEqual(["early", "late"]);
  });
  it("query_key 为空（旧缓存）不参与去重", () => {
    const rows = mergeEvidence(
      [cache({ id: "cs1", query_key: "" }), cache({ id: "cs2", query_key: "" })], [],
    );
    expect(rows).toHaveLength(2);
  });
  it("实时行用 tools store order 层内稳定（R15 规划期修正）", () => {
    const rows = mergeEvidence(
      [],
      [live({ call_id: "cB" }), live({ call_id: "cA" })],
    );
    expect(rows.map((r) => r.key)).toEqual(["cB", "cA"]);  // 传入序即 store order
  });
  it("未配对实时行排在同回合缓存行之后（层内不重排缓存序）", () => {
    const rows = mergeEvidence(
      [cache({ id: "cs1", cached_at: "2026-10-19T01:00:00.000001", turn_id: "t1" })],
      [live({ call_id: "c9", turnId: "t1" })],
    );
    expect(rows.map((r) => r.key)).toEqual(["cs1", "c9"]);
  });
});

describe("准入（C2）", () => {
  it("hidden 级不入证据 tab（amap_geocode）；未注册工具默认不入", () => {
    const rows = mergeEvidence(
      [cache({ id: "csH", tool: "amap_geocode" }), cache({ id: "csU", tool: "unknown_tool" })],
      [],
    );
    expect(rows).toHaveLength(0);
  });
  it("collapsed 级缓存行入列并透传 truncated/total（Task 5 派生字段）", () => {
    const rows = mergeEvidence(
      [cache({ id: "cs1", empty: false, total: 3, truncated: false })], [],
    );
    expect(rows[0].empty).toBe(false);
    expect(rows[0].total).toBe(3);
  });
  it("empty===true 的缓存行剔除（C7-2 空结果过滤，用户裁决 3 全过滤）", () => {
    const rows = mergeEvidence(
      [cache({ id: "cs1", empty: true, total: 0 })], [],
    );
    expect(rows).toHaveLength(0);
  });
  it("empty===null（旧索引 unknown）不预过滤，透传为 undefined 待展开后按 data 判（R12）", () => {
    const rows = mergeEvidence(
      [cache({ id: "cs1", empty: null, total: null })], [],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].empty).toBeUndefined();
  });
  it("空结果实时行（items=[] 且 total=0 且无 answer）剔除；answer 不算空（C7-2/R1）", () => {
    const rows = mergeEvidence([], [
      live({ call_id: "cE", args: { query: "无结果词" },
            data: { items: [], total: 0 } }),
      live({ call_id: "cA", args: { query: "有答案" },
            data: { answer: "## 答案", items: [], total: 0 } }),
    ]);
    expect(rows.map((r) => r.key)).toEqual(["cA"]);
  });
  it("无专用卡的 collapsed 工具剔除（C7-1：裸 JSON 不进证据 tab）", () => {
    const rows = mergeEvidence(
      [cache({ id: "csF", tool: "amap_text_search", query_key: "" })], [],
    );
    expect(rows).toHaveLength(0);
  });
});

describe("C05 文献空/失败行可达（补检入口不被过滤）", () => {
  it("search_literature 空结果缓存行仍入列（LiteratureCard 空态自带补检 hint）", () => {
    const rows = mergeEvidence(
      [cache({ id: "csL", tool: "search_literature", query: "宗族 文献", empty: true })],
      [],
    );
    expect(rows.map((r) => r.key)).toEqual(["csL"]);
    expect(rows[0].empty).toBe(true);
  });
  it("search_literature 失败实时行入列并标记 failed（其余工具维持剔除）", () => {
    const rows = mergeEvidence(
      [],
      [live({ call_id: "cLit", tool: "search_literature", status: "failed", ok: false }),
       live({ call_id: "cHot", tool: "search_hotels", status: "failed", ok: false })],
    );
    expect(rows.map((r) => r.key)).toEqual(["cLit"]);
    expect(rows[0].failed).toBe(true);
  });
  it("search_literature 失败且零命中：入列、标 empty 而非 failed（尾标落「空结果」）", () => {
    const rows = mergeEvidence(
      [],
      [live({ call_id: "cZero", tool: "search_literature", status: "failed", ok: false,
              data: { items: [], total: 0 } })],
    );
    expect(rows.map((r) => r.key)).toEqual(["cZero"]);
    expect(rows[0].empty).toBe(true);
    expect(rows[0].failed).toBeUndefined();
  });
  it("不同 query_key 的两次文献检索各占一行（范围/筛选可区分，C04 验收）", () => {
    const rows = mergeEvidence(
      [cache({ id: "cs1", tool: "search_literature",
               query_key: "search_literature|{\"query\":\"宗族\"}" }),
       cache({ id: "cs2", tool: "search_literature",
               query_key: "search_literature|{\"query\":\"宗族\",\"year\":\"2020\"}" })],
      [],
    );
    expect(rows).toHaveLength(2);
  });
});

describe("实时行查询短语（liveQuery 回落链）", () => {
  it("住宿搜索无 query 键时回落 dest_name（C07b：证据行标题不留空）", () => {
    const rows = mergeEvidence(
      [],
      [live({ call_id: "cH", tool: "search_hotels", args: { dest_name: "大理" } })],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].query).toBe("大理");
  });
});
