import type { ComponentType } from "react";
import { describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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
import { LiteratureCard } from "../src/components/cards/LiteratureCard";
import { ProposalCard } from "../src/components/cards/ProposalCard";
import { UpdateResearchContextCard } from "../src/components/cards/UpdateResearchContextCard";
import { saveKeys } from "../src/lib/keys";
import { useExportFormatStore } from "../src/stores/exportFormat";

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
// 补充断言：注册表 key 必须与真实工具名全等（不臆造、不漏映射）。
// 名单分两组：内核名单取自实测 `fieldwork_agent.kernel.CAPABILITIES()`
// （kernel/registry.py + kernel/sources/flyai.py + kernel/aggregation/* +
// kernel/documents/* + kernel/sources/amap.py，17 项，与 mcp_server/mapping.py TOOL_GROUPS 键集一致）；
// 宿主层状态工具（轮 2 症状②）由 web_host/agent/state_tools.py 每回合注入
// loop，不经内核 registry，是注册表唯一超出内核键集的合法例外。
// ──────────────────────────────────────────────────────────────────────────
const KERNEL_TOOLS = [
  "profile_field_site",
  "compose_itinerary",
  "plan_last_mile",
  "analyze_fieldwork_window",
  "estimate_living_cost",
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
  "search_literature",
  "amap_text_search",
];

/** 宿主层回合内状态工具（web_host/agent/state_tools.py build_state_tools）。 */
const HOST_STATE_TOOLS = ["update_research_context"];

describe("registry 与内核对齐", () => {
  test("注册表键集 = 内核 18 能力名 + 宿主状态工具（无臆造 key）", () => {
    expect(Object.keys(toolCardRegistry).sort()).toEqual(
      [...KERNEL_TOOLS, ...HOST_STATE_TOOLS].sort(),
    );
  });
  test("每个注册工具都命中专属卡片（不落 Fallback）", () => {
    for (const tool of [...KERNEL_TOOLS, ...HOST_STATE_TOOLS]) {
      expect(getToolCard(tool)).not.toBe(ToolCardFallback);
    }
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
  const PLAN_LINKED = {
    ...PLAN,
    legs: [{
      ...PLAN.legs[0],
      chosen: { ...PLAN.legs[0].chosen, jump_url: "https://flight.example/mu5843" },
    }],
    stays: [{
      ...PLAN.stays[0],
      chosen: { ...PLAN.stays[0].chosen, jump_url: "https://hotel.example/x" },
    }],
    alternatives: [{
      from: "昆明", to: "喜洲",
      items: [{ name: "G2937", price: 350, mode: "train", price_raw: "¥350", extra: {},
                jump_url: "https://train.example/y" }],
    }],
  };
  test("chosen/备选带 jump_url 时链接化并显示时效提示（A3+D11）", () => {
    render(<ItineraryCard data={PLAN_LINKED} />);
    const legLink = screen.getByRole("link", { name: "MU5843" });   // 交通腿 chosen 名称链接化（驻留项补盖）
    expect(legLink).toHaveAttribute("href", "https://flight.example/mu5843");
    expect(legLink).toHaveAttribute("target", "_blank");
    const stayLink = screen.getByRole("link", { name: "喜洲客栈" });
    expect(stayLink).toHaveAttribute("href", "https://hotel.example/x");
    expect(stayLink).toHaveAttribute("target", "_blank");
    fireEvent.click(screen.getByText("备选路线（1）"));   // 展开折叠区（jsdom 不自动展开 details）
    const altLink = screen.getByRole("link", { name: "G2937" });
    expect(altLink).toHaveAttribute("href", "https://train.example/y");
    expect(screen.getByText("下单前请以飞猪页面为准")).toBeInTheDocument();
  });
  test("无链接 PLAN 不产生链接与时效提示；时戳只显日期", () => {
    render(<ItineraryCard data={PLAN} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/下单前请以飞猪页面为准/)).not.toBeInTheDocument();
    expect(screen.getByText("2026-10-15")).toBeInTheDocument();   // generated_at 截 yyyy-mm-dd
  });
  // N2 末端接驳（PoC2 §3.3）：有 options 显示"公交 × N 段 / 最低价 ¥x / 约 y 分钟"，
  // 无 options 显示内核 note 原文；无 last_mile 键的老数据不渲染接驳行。
  const PLAN_LAST_MILE = {
    ...PLAN,
    legs: [{
      ...PLAN.legs[0],
      last_mile: {
        hub_geo: { location: "100.1,25.6", city: "昆明" },
        site_geo: { location: "100.5,25.9", city: "喜洲" },
        options: {
          items: [{ name: "8路", price: 8, schedule: { duration_minutes: 60 } }],
          total: 2, cheapest: 8, dearest: 12,
        },
        note: "",
      },
    }],
  };
  test("N2 接驳行：有 options 显示 公交×N段/¥x/约y分钟（ItemPage.cheapest）", () => {
    render(<ItineraryCard data={PLAN_LAST_MILE} />);
    expect(screen.getByText("末端接驳")).toBeInTheDocument();
    expect(screen.getByText(/公交 × 2 段/)).toBeInTheDocument();
    expect(screen.getByText(/约 60 分钟/)).toBeInTheDocument();
    expect(screen.getByText("¥8")).toBeInTheDocument();
  });
  test("N2 接驳行：无 options 如实转述内核 note 原文（灰字，不 ¥0）", () => {
    render(<ItineraryCard data={{
      ...PLAN,
      legs: [{
        ...PLAN.legs[0],
        last_mile: { hub_geo: null, site_geo: null, options: null,
                     note: "未配置高德 API Key，接驳查询已停用" },
      }],
    }} />);
    expect(screen.getByText(/未配置高德 API Key，接驳查询已停用/)).toBeInTheDocument();
    expect(screen.queryByText(/公交 × /)).not.toBeInTheDocument();
  });
  test("N2 接驳行：无 last_mile 键的老数据不渲染接驳行", () => {
    render(<ItineraryCard data={PLAN} />);
    expect(screen.queryByText("末端接驳")).not.toBeInTheDocument();
  });
  // N6 接驳示意地图（PoC2 §5.2 嵌入点二）：hub/site 双坐标 → 地图块；
  // 无 Key 降级为坐标文字，不让接驳信息丢失。
  test("N6 接驳地图：hub/site 双坐标 → 渲染接驳示意块（无 Key 降级）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      ({ ok: true, json: async () => ({ amap_js_key: "", amap_js_security: "" }) } as unknown as Response)));
    render(<ItineraryCard data={PLAN_LAST_MILE} />);
    expect(await screen.findByText(/未配置高德 JS API Key/)).toBeInTheDocument();
    expect(screen.getByText("100.1,25.6")).toBeInTheDocument();
    vi.unstubAllGlobals();
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
  // N6 C4 双源标注（PoC2 §4.4）：geo 有值 → 「高德底座」徽标 + 周边设施地图块；
  // facilities 带 facility_kind（kernel 附着）与 extra.location（POI 坐标）。
  test("SiteProfileCard geo 有值 → C4 高德底座徽标 + 周边设施地图块", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      ({ ok: true, json: async () => ({ amap_js_key: "", amap_js_security: "" }) } as unknown as Response)));
    render(<SiteProfileCard data={{
      location: "喜洲镇",
      geo: { location: "100.196,25.047", province: "云南省" },
      facilities: { items: [
        { name: "喜洲卫生院", extra: { location: "100.20,25.05", facility_kind: "卫生院 诊所" } },
      ], total: 1 },
      transport: null, accommodation: null, culture: [], amenities: null,
      summary: { section_counts: {}, accommodation_nightly_min: null, transport_min: null, hints: {} },
    }} />);
    expect(screen.getByText("高德底座")).toBeInTheDocument();
    expect(await screen.findByText(/未配置高德 JS API Key/)).toBeInTheDocument();  // 无 Key 降级卡片
    expect(screen.getByText("喜洲卫生院")).toBeInTheDocument();                    // 设施清单仍可读
    vi.unstubAllGlobals();
  });
  test("SiteProfileCard geo 缺段 → C4 文本回退徽标，无地图块", () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      ({ ok: true, json: async () => ({ amap_js_key: "", amap_js_security: "" }) } as unknown as Response)));
    render(<SiteProfileCard data={{
      location: "Chiang Mai", transport: null, accommodation: null, culture: [], amenities: null,
      summary: { section_counts: {}, accommodation_nightly_min: null, transport_min: null, hints: {} },
    }} />);
    expect(screen.getByText("文本回退")).toBeInTheDocument();
    expect(screen.queryByText(/未配置高德 JS API Key/)).not.toBeInTheDocument();
    vi.unstubAllGlobals();
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
    expect(screen.getByText(/开题报告/)).toBeInTheDocument();
    expect(screen.getByText(/英美式八章/)).toBeInTheDocument();
    expect(screen.getByText("研究定位")).toBeInTheDocument();
  });
  test("UpdateResearchContextCard 渲染已写入字段标签与依据行（轮 2 症状②）", () => {
    render(<UpdateResearchContextCard data={{
      updated: ["budget", "research_questions"],
      reason: "用户确认预算为 5 万，并凝练 2-3 个研究问题",
    }} />);
    expect(screen.getByText(/研究定位已更新/)).toBeInTheDocument();
    expect(screen.getByText("经费")).toBeInTheDocument();
    expect(screen.getByText("研究问题")).toBeInTheDocument();
    expect(screen.getByText(/用户确认预算为 5 万/)).toBeInTheDocument();
  });
  test("未知字段名原样呈现（不臆造标签），无 reason 不渲染依据行", () => {
    render(<UpdateResearchContextCard data={{ updated: ["custom_field"] }} />);
    expect(screen.getByText("custom_field")).toBeInTheDocument();
    expect(screen.queryByText(/依据/)).not.toBeInTheDocument();
  });
  test("data 为 null（工具失败）时各卡不抛错", () => {
    const cards: ComponentType<ToolCardProps>[] = [
      SiteProfileCard, PoiCard, HotelCard, FlightCard, TrainCard,
      ItineraryCard, BudgetCard, InformantCard, ProposalCard, ToolCardFallback,
      UpdateResearchContextCard, LiteratureCard,
    ];
    for (const C of cards) {
      expect(() => render(<C data={null} degraded="flyai_unavailable" />)).not.toThrow();
    }
  });
});

// ──────────────────────────────────────────────────────────────────────────
// 终审#1：ProposalCard 下载锚点必须 preventDefault 后走 fetch + authHeaders()
// （/projects 前缀受 F02 令牌保护，静态锚点导航带不了 X-App-Token，必 401）。
// ──────────────────────────────────────────────────────────────────────────
describe("ProposalCard 下载入口（终审#1）", () => {
  test("点击下载走 fetch 注入 X-App-Token，blob → objectURL 临时链", async () => {
    localStorage.clear();
    useExportFormatStore.setState({ format: "md" });
    saveKeys("", "", "tok-7"); // authHeaders() 在点击时读取：新存令牌即时生效
    const calls: Array<[string, RequestInit | undefined]> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return { ok: true, blob: async () => new Blob(["# 研究方案"]) } as unknown as Response;
    }));
    // jsdom 未实现 createObjectURL/revokeObjectURL，注入可断言替身
    const createObjectURL = vi.fn(() => "blob:card-md");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true, writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true, writable: true });

    render(<ProposalCard data={{ markdown: "# 研究方案\n", kind: "proposal", version: "draft", sections: ["研究定位"] }} projectId="pc1" />);
    fireEvent.click(screen.getByRole("link", { name: /下载 \.md/ }));

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(calls[0][0]).toBe("/projects/pc1/proposal/download");
    expect(calls[0][1]?.headers).toMatchObject({ "X-App-Token": "tok-7" });
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:card-md");
    vi.unstubAllGlobals();
  });

  test("偏好 docx 时：链接文案与 href 追加 ?format=docx", async () => {
    localStorage.clear();
    saveKeys("", "", "tok-7");
    useExportFormatStore.setState({ format: "docx" });
    const calls: Array<[string, RequestInit | undefined]> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return { ok: true, blob: async () => new Blob(["# 研究方案"]) } as unknown as Response;
    }));
    // jsdom 未实现 createObjectURL/revokeObjectURL，注入可断言替身
    const createObjectURL = vi.fn(() => "blob:card-docx");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true, writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true, writable: true });

    render(<ProposalCard data={{ markdown: "# 研究方案\n", kind: "proposal", version: "draft", sections: ["研究定位"] }} projectId="pc1" />);
    const link = screen.getByRole("link", { name: /下载 \.docx/ });
    expect(link).toHaveAttribute("href", "/projects/pc1/proposal/download?format=docx");
    fireEvent.click(link);

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(calls[0][0]).toBe("/projects/pc1/proposal/download?format=docx");
    expect(calls[0][1]?.headers).toMatchObject({ "X-App-Token": "tok-7" });
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:card-docx");
    useExportFormatStore.setState({ format: "md" });
    vi.unstubAllGlobals();
  });
});

describe("SearchResultCard 失败原因透传", () => {
  test("error.hint 优先于通用无供给文案", () => {
    render(<TrainCard data={null} error={{
      code: "flyai_invalid_date",
      message: "出发日期非法",
      hint: "实时搜索仅支持今天（2026-10-18）及以后的日期；请改用 2027-07-15 重试",
    }} />);
    expect(screen.getByText(/仅支持今天（2026-10-18）及以后/)).toBeInTheDocument();
    expect(screen.queryByText(/本次查询无可用供给/)).not.toBeInTheDocument();
  });

  test("只有 message 时展示 message", () => {
    render(<TrainCard data={null} error={{ code: "flyai_error", message: "服务暂不可用" }} />);
    expect(screen.getByText(/服务暂不可用/)).toBeInTheDocument();
  });

  test("无 error 时保持原文案", () => {
    render(<TrainCard data={null} />);
    expect(screen.getByText(/本次查询无可用供给/)).toBeInTheDocument();
  });
});

describe("BudgetCard 估算区间", () => {
  const RANGE_BUDGET = {
    items: [
      { name: "住宿（大理）", amount: 4350, source: "cheapest", currency: "CNY",
        unit: "每晚价×夜数×房间数", quantity: 29, people: 1, priced_at: "", source_ref: "" },
      { name: "餐饮（估算）", amount: 540, amount_low: 540, amount_high: 900,
        source: "estimate", currency: "CNY", unit: "元/人·天×人数×天数",
        quantity: 30, people: 1, priced_at: "", basis: "村内混合；云南农村人均食品烟酒 5,351 元/年" },
    ],
    total: 4890, total_high: 5250, budget_total: 20000,
    over_budget: false, over_budget_possible: false,
    coverage: "complete", uncovered: [],
  };

  test("条目渲染区间与口径", () => {
    render(<BudgetCard data={RANGE_BUDGET} />);
    expect(screen.getByText("¥540–¥900")).toBeInTheDocument();
    expect(screen.getByText(/云南农村人均食品烟酒/)).toBeInTheDocument();
    expect(screen.getByText(/上限约 ¥5250/)).toBeInTheDocument();
  });

  test("区间跨预算线显示「跨预算线」且不显示「预算内」", () => {
    render(<BudgetCard data={{ ...RANGE_BUDGET, budget_total: 5000, over_budget_possible: true }} />);
    expect(screen.getByText("跨预算线")).toBeInTheDocument();
    expect(screen.queryByText("预算内")).not.toBeInTheDocument();
    expect(screen.getByText(/按上限.*可能超出/)).toBeInTheDocument();
  });
});

// ───────────────────────────────────────────────────────────────
// LiteratureCard（search_literature → LiteraturePage）：文献条目如实呈现 + 中文社科深链。
// 红线：只呈现检索到的真实条目，空字段不渲染、不臆造。
// ───────────────────────────────────────────────────────────────
describe("LiteratureCard", () => {
  const LIT = {
    items: [
      { id: "https://openalex.org/W1", title: "The social life of things",
        authors: ["Arjun Appadurai", "Igor Kopytoff"], year: 1986,
        doi: "10.1017/CBO9780511812682", venue: "Cambridge University Press",
        cited_by_count: 1234, abstract: "商品的社会生命与 commodification。",
        open_access_url: "https://example.org/oa.pdf", is_oa: true },
    ],
    total: 25,
    chinese_sources: [
      { label: "国家哲学社会科学文献中心", url: "https://www.ncpssd.cn/" },
      { label: "PubScholar 公益学术平台", url: "https://pubscholar.cn/search?q=%E9%93%B6%E5%99%A8" },
    ],
  };

  test("渲染文献条目：标题/作者/年份/期刊/被引", () => {
    render(<LiteratureCard data={LIT} />);
    expect(screen.getByText(/The social life of things/)).toBeInTheDocument();
    expect(screen.getByText(/Arjun Appadurai/)).toBeInTheDocument();
    expect(screen.getByText(/1986/)).toBeInTheDocument();
    expect(screen.getByText(/Cambridge University Press/)).toBeInTheDocument();
    expect(screen.getByText(/1234/)).toBeInTheDocument();
  });

  test("总数 chip 与 DOI/OA 链接如实呈现", () => {
    render(<LiteratureCard data={LIT} />);
    expect(screen.getByText(/25/)).toBeInTheDocument();  // total 直出
    const doi = screen.getByRole("link", { name: /DOI/ });
    expect(doi).toHaveAttribute("href", "https://doi.org/10.1017/CBO9780511812682");
    expect(screen.getByRole("link", { name: /开放获取|OA|全文/ })).toHaveAttribute(
      "href", "https://example.org/oa.pdf");
  });

  test("中文社科检索深链呈现为可点击链接", () => {
    render(<LiteratureCard data={LIT} />);
    const ncpssd = screen.getByRole("link", { name: /国家哲学社会科学文献中心/ });
    expect(ncpssd).toHaveAttribute("href", "https://www.ncpssd.cn/");
    const pub = screen.getByRole("link", { name: /PubScholar/ });
    expect(pub).toHaveAttribute("href", "https://pubscholar.cn/search?q=%E9%93%B6%E5%99%A8");
  });

  test("data=null 不报错且走空态", () => {
    render(<LiteratureCard data={null} />);
    expect(screen.getByText("工具未返回数据")).toBeInTheDocument();
  });
});
