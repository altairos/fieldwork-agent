import type { ComponentType } from "react";
import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { getToolCard, toolCardRegistry, type ToolCardProps } from "../src/components/cards/registry";
import { HotelCard } from "../src/components/cards/HotelCard";
import { BudgetCard } from "../src/components/cards/BudgetCard";
import { FlightCard } from "../src/components/cards/FlightCard";
import { ToolCardFallback } from "../src/components/cards/ToolCardFallback";
import { SiteProfileCard } from "../src/components/cards/SiteProfileCard";
import { PoiCard } from "../src/components/cards/PoiCard";
import { TrainCard } from "../src/components/cards/TrainCard";
import { ItineraryCard } from "../src/components/cards/ItineraryCard";
import { InformantCard } from "../src/components/cards/InformantCard";
import { ProposalCard } from "../src/components/cards/ProposalCard";

describe("registry", () => {
  test("已知工具映射到卡片", () => {
    expect(getToolCard("search_hotels")).toBe(HotelCard);
  });
  test("未知工具回退 Fallback", () => {
    const C = getToolCard("nonexistent_tool");
    render(<C data={{ foo: 1 }} />);
    expect(screen.getByText(/nonexistent|foo|{/i)).toBeInTheDocument(); // fallback 显示 JSON
  });
});

describe("HotelCard 价格不失真", () => {
  test("有报价显示 ¥ + 来源", () => {
    render(<HotelCard data={{ items: [{ name: "大理客栈", price: 180, price_raw: "¥180", jump_url: null, pic_url: null, extra: {} }], total: 1, cheapest: 180, dearest: 180 }} />);
    expect(screen.getByText("大理客栈")).toBeInTheDocument();
    expect(screen.getByText("¥180")).toBeInTheDocument();
  });
  test("price=null 显示暂无报价(非 ¥0)", () => {
    render(<HotelCard data={{ items: [{ name: "某酒店", price: null, price_raw: null, jump_url: null, pic_url: null, extra: {} }], total: 1, cheapest: null, dearest: null }} />);
    expect(screen.getByText("暂无报价")).toBeInTheDocument();
    expect(screen.queryByText("¥0")).not.toBeInTheDocument();
  });
});

describe("FlightCard 降级", () => {
  test("degraded 显示降级提示", () => {
    render(<FlightCard data={{ items: [], total: 0, cheapest: null, dearest: null }} degraded="flyai_unavailable" />);
    expect(screen.getByText(/降级|flyai_unavailable|暂无报价|替代/i)).toBeInTheDocument();
  });
});

describe("BudgetCard coverage", () => {
  test("coverage=partial 显示预算不完整 + uncovered", () => {
    render(<BudgetCard data={{ items: [{ name: "住宿", amount: 13500, source: "cheapest", currency: "CNY", unit: "晚", quantity: 90, people: 1, priced_at: "", source_ref: "" }], total: 13500, budget_total: 20000, over_budget: false, coverage: "partial", uncovered: ["市内交通"] }} />);
    expect(screen.getByText(/不完整|partial|未覆盖/i)).toBeInTheDocument();
    expect(screen.getByText(/市内交通/)).toBeInTheDocument();
  });
  test("over_budget=true 显示超支警示", () => {
    render(<BudgetCard data={{ items: [], total: 25000, budget_total: 20000, over_budget: true, coverage: "complete", uncovered: [] }} />);
    expect(screen.getByText(/超支|超出|over/i)).toBeInTheDocument();
  });
});

// ──────────────────────────────────────────────────────────────────────────
// 补充断言：注册表 key 必须与内核 registry 注册名全等（不臆造、不漏映射）。
// 名单取自实测 `fieldwork_agent.kernel.CAPABILITIES()`（kernel/registry.py +
// kernel/sources/flyai.py + kernel/aggregation/* + kernel/documents/*，13 项），
// 与 mcp_server/mapping.py TOOL_GROUPS 的键集一致。
// ──────────────────────────────────────────────────────────────────────────
const KERNEL_TOOLS = [
  "profile_field_site",
  "compose_itinerary",
  "compose_proposal",
  "compose_interview_guide",
  "suggest_informants",
  "search_flights",
  "search_trains",
  "search_hotels",
  "search_marriott_hotels",
  "search_marriott_packages",
  "search_pois",
  "search_keyword",
  "search_ai",
];

describe("registry 与内核对齐", () => {
  test("注册表键集 = 内核 13 能力名（无臆造 key）", () => {
    expect(Object.keys(toolCardRegistry).sort()).toEqual([...KERNEL_TOOLS].sort());
  });
  test("每个内核工具都命中专属卡片（不落 Fallback）", () => {
    for (const tool of KERNEL_TOOLS) expect(getToolCard(tool)).not.toBe(ToolCardFallback);
  });
});

const PLAN = {
  departure: "昆明",
  destinations: ["喜洲"],
  start_date: "2026-06-01",
  end_date: "2026-08-29",
  legs: [{
    from: "昆明", to: "喜洲", date: "2026-06-01", direction: "outbound",
    chosen: { name: "MU5843", price: 620, mode: "flight", price_raw: "¥620", extra: {} },
    alternatives: [], note: "",
  }],
  stays: [{
    dest: "喜洲", check_in: "2026-06-01", check_out: "2026-08-29", nights: 89,
    nightly_price: 150, chosen: { name: "喜洲客栈", price: 150, mode: "民宿", extra: {} },
    alternatives: [], long_stay_unverified: true,
  }],
  rhythm: [
    { label: "适应期", days: 9, note: "报到、熟悉村落格局" },
    { label: "深入期", days: 45, note: "参与式观察与深度访谈" },
    { label: "补充期", days: 24, note: "补访缺口、整理笔记" },
  ],
  buffer_days: 9,
  alternatives: [{ from: "昆明", to: "喜洲", items: [{ name: "G2937", price: 350, mode: "train", price_raw: "¥350", extra: {} }] }],
  budget: {
    items: [
      { name: "交通（昆明→喜洲）", amount: 620, source: "cheapest", currency: "CNY", unit: "票价×人数", quantity: 1, people: 1, priced_at: "2026-10-15T08:00:00", source_ref: "MU5843" },
      { name: "住宿（喜洲）", amount: 13500, source: "cheapest", currency: "CNY", unit: "每晚价×夜数×房间数", quantity: 90, people: 1, priced_at: "2026-10-15T08:00:00", source_ref: "喜洲客栈" },
    ],
    total: 14120, budget_total: 20000, over_budget: false, coverage: "complete", uncovered: [],
  },
  generated_at: "2026-10-15T08:00:00",
  inputs_hash: "0f3c1a9b7e5d4c2b",
};

describe("ItineraryCard 时间线与预算", () => {
  test("节奏分段竖向时间线 + Day 区间 + 缓冲日", () => {
    render(<ItineraryCard data={PLAN} />);
    expect(screen.getByText("适应期")).toBeInTheDocument();
    expect(screen.getByText("Day 1–9")).toBeInTheDocument();
    expect(screen.getByText("深入期")).toBeInTheDocument();
    expect(screen.getByText("缓冲日")).toBeInTheDocument();
    expect(screen.getByText("预算累计")).toBeInTheDocument();
  });
  test("价格与 payload 逐字相等（不失真）", () => {
    render(<ItineraryCard data={PLAN} />);
    expect(screen.getByText("¥150")).toBeInTheDocument();      // stays.nightly_price
    expect(screen.getByText("¥13500")).toBeInTheDocument();    // budget.items[1].amount
    expect(screen.getByText("预算内")).toBeInTheDocument();      // coverage=complete 且未超支
  });
});

describe("其余卡片", () => {
  test("SiteProfileCard 渲染地点名与无供给二段式建议", () => {
    render(<SiteProfileCard data={{
      location: "喜洲镇", transport: null, accommodation: null, culture: [], amenities: null,
      summary: {
        section_counts: { culture: 5, accommodation: 0, transport: 0, amenities: 0 },
        accommodation_nightly_min: null, transport_min: null,
        hints: { accommodation: "平台未见住宿供给：建议以最近枢纽城市住宿为基地" },
      },
      generated_at: "",
    }} />);
    expect(screen.getByText("喜洲镇")).toBeInTheDocument();
    expect(screen.getByText(/平台未见住宿供给/)).toBeInTheDocument();
  });
  test("InformantCard 渲染候选类型与提问域", () => {
    render(<InformantCard data={[{
      type: "村落长老", approach: "经介绍人引荐后首访", cautions: "避免在宗族纠纷中站队",
      question_domains: ["宗族组织", "祭祖仪式"], ethics: "知情同意，录音需授权",
      access_channel: "村委会引荐", preparation: "先读族谱碑刻资料",
    }]} />);
    expect(screen.getByText("村落长老")).toBeInTheDocument();
    expect(screen.getByText("宗族组织")).toBeInTheDocument();
    expect(screen.getByText(/知情同意/)).toBeInTheDocument();
  });
  test("ProposalCard 显示 kind/version 与体例限制", () => {
    render(<ProposalCard data={{
      markdown: "# 研究方案\n", kind: "proposal", version: "draft", sections: ["研究定位"],
      requested_mode: "kaiti", mode: "global", inputs_hash: "abc",
    }} />);
    expect(screen.getByText("draft")).toBeInTheDocument();
    expect(screen.getByText(/kaiti/)).toBeInTheDocument();
    expect(screen.getByText("研究定位")).toBeInTheDocument();
  });
  test("data 为 null（工具失败）时各卡不抛错", () => {
    const cards: ComponentType<ToolCardProps>[] = [
      SiteProfileCard, PoiCard, HotelCard, FlightCard, TrainCard,
      ItineraryCard, BudgetCard, InformantCard, ProposalCard, ToolCardFallback,
    ];
    for (const C of cards) {
      expect(() => render(<C data={null} degraded="flyai_unavailable" />)).not.toThrow();
    }
  });
});
