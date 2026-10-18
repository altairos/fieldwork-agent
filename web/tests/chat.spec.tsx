import { describe, expect, test, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { Timeline } from "../src/components/chat/Timeline";
import { ToolActivityStrip } from "../src/components/chat/ToolActivityStrip";
import { BudgetPanel } from "../src/components/right/BudgetPanel";
import { FieldSitesPanel } from "../src/components/right/FieldSitesPanel";
import { useConversationStore } from "../src/stores/conversation";
import { useToolsStore } from "../src/stores/tools";
import { useProjectStore } from "../src/stores/project";

beforeEach(() => {
  useConversationStore.getState().reset();
  useToolsStore.getState().reset();
  useProjectStore.getState().reset();
});

describe("Timeline", () => {
  test("渲染用户+助手历史消息", () => {
    useConversationStore.setState({ messages: [
      { role: "user", content: "帮我排行程" },
      { role: "assistant", content: "已排好" },
    ], streamBuffer: "", turnRunning: false, lastUsage: {} });
    render(<Timeline />);
    expect(screen.getByText("帮我排行程")).toBeInTheDocument();
    expect(screen.getByText(/已排好/)).toBeInTheDocument();
  });
  test("流式缓冲渲染", () => {
    useConversationStore.setState({ messages: [], streamBuffer: "正在搜索", turnRunning: true, lastUsage: {} });
    render(<Timeline />);
    expect(screen.getByText(/正在搜索/)).toBeInTheDocument();
  });
});

describe("ToolActivityStrip", () => {
  test("渲染工具调用状态", () => {
    useToolsStore.getState().applyEvent({ event: "tool_started", call_id: "c1", tool: "search_hotels", args: {}, purpose: "住宿" });
    render(<ToolActivityStrip />);
    expect(screen.getByText(/search_hotels/)).toBeInTheDocument();
  });
  test("降级工具显示降级标记", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c2", tool: "search_trains", args: {}, purpose: "" });
    s.applyEvent({ event: "tool_completed", call_id: "c2", ok: false, data: null, degraded: "flyai_unavailable" });
    render(<ToolActivityStrip />);
    expect(screen.getByText(/降级|flyai_unavailable/i)).toBeInTheDocument();
  });
});

describe("右栏面板同源", () => {
  test("BudgetPanel 无行程显示空状态", () => {
    render(<BudgetPanel />);
    expect(screen.getByText(/尚未|暂无|空/i)).toBeInTheDocument();
  });
  test("FieldSitesPanel 渲染快照站点", () => {
    useProjectStore.getState().loadSnapshot({
      project_id: "p1", phase: "field_site_profiles", research_context: {},
      field_sites: [{ name: "喜洲镇", confirmed: true, profile: null }],
      informants: [], proposals: [], phase_history: [],
    });
    render(<FieldSitesPanel />);
    expect(screen.getByText(/喜洲镇/)).toBeInTheDocument();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 补充断言（依据设计红线与 store 真实签名）：中栏组件细节 + 右栏五面板 +
// RightDock 接线（Tab id 对齐 ui store RightTab、切换建议不自动切）。
// ══════════════════════════════════════════════════════════════════════════
import { AssistantStream } from "../src/components/chat/AssistantStream";
import { MessageBubble } from "../src/components/chat/MessageBubble";
import { PhaseBanner } from "../src/components/chat/PhaseBanner";
import { Composer, type TurnHandle } from "../src/components/chat/Composer";
import { InformantsPanel } from "../src/components/right/InformantsPanel";
import { KnowledgePanel } from "../src/components/right/KnowledgePanel";
import { BasicInfoPanel } from "../src/components/right/BasicInfoPanel";
import { RightDock } from "../src/components/layout/RightDock";
import { useUiStore } from "../src/stores/ui";
import type { ProjectSnapshot } from "../src/types/models";

/** 内核 FieldSiteProfile 真实形状（kernel/aggregation/site_profile.py）。 */
const SITE_PROFILE = {
  location: "喜洲镇",
  transport: {
    items: [{ name: "昆明 → 大理 · MU5843", price: 620, price_raw: "¥620", jump_url: null, pic_url: null, extra: { 时长: "55min" } }],
    total: 1, cheapest: 620, dearest: 620,
  },
  accommodation: {
    items: [{ name: "喜洲客栈·院", price: 150, price_raw: "¥150", jump_url: null, pic_url: null, extra: {} }],
    total: 1, cheapest: 150, dearest: 150,
  },
  culture: [{ name: "严家大院", category: "院落" }, { name: "本主庙", category: "宗教" }],
  amenities: null,
  summary: { section_counts: { culture: 2 }, accommodation_nightly_min: 150, transport_min: 620, hints: {} },
  generated_at: "2026-10-15T09:00:00Z",
};

/** ItineraryPlan（kernel/aggregation/itinerary.py：预算随行程返回，无独立预算能力）。 */
const PLAN = {
  departure: "昆明", destinations: ["喜洲镇"], start_date: "2026-11-01", end_date: "2027-01-28",
  legs: [], stays: [], rhythm: [], buffer_days: 9, alternatives: [],
  budget: {
    items: [
      { name: "住宿（90晚×¥150）", amount: 13500, source: "cheapest", currency: "CNY", unit: "晚", quantity: 90, people: 1, priced_at: "2026-10-15", source_ref: "search_hotels" },
      { name: "市内与餐饮", amount: 3000, source: "estimate", currency: "CNY", unit: "天", quantity: 90, people: 1, priced_at: "2026-10-15", source_ref: "" },
      { name: "往返交通", amount: 1240, source: "search", currency: "CNY", unit: "程", quantity: 2, people: 1, priced_at: "2026-10-15", source_ref: "search_flights" },
    ],
    total: 17740, budget_total: 20000, over_budget: false, coverage: "complete", uncovered: [],
  },
  generated_at: "2026-10-15T10:00:00Z", inputs_hash: "abc123",
};

function snapshot(patch: Partial<ProjectSnapshot> = {}): ProjectSnapshot {
  return {
    project_id: "p1", phase: "research_positioning", research_context: {},
    field_sites: [], informants: [], proposals: [], phase_history: [], ...patch,
  };
}

function turnHandle(patch: Partial<TurnHandle> = {}): TurnHandle {
  return { send: () => {}, cancel: () => {}, busy: false, conflict: false, ...patch };
}

describe("MessageBubble / AssistantStream", () => {
  test("user 气泡右对齐 indigo（设计板 436-440）", () => {
    render(<MessageBubble role="user" content="我从昆明出发" />);
    expect(screen.getByText("我从昆明出发").className).toContain("bg-indigo");
  });
  test("assistant 正文走 markdown（列表/加粗）", () => {
    render(<MessageBubble role="assistant" content={"分三段推进：\n\n- **适应期** 9 天\n- 深入期 45 天"} />);
    expect(screen.getByText(/适应期/)).toBeInTheDocument();
    expect(screen.getByText(/深入期 45 天/)).toBeInTheDocument();
  });
  test("turnRunning 时流式缓冲带光标，无缓冲且未运行时不渲染", () => {
    const { container, rerender } = render(<AssistantStream />);
    expect(container.firstChild).toBeNull();
    act(() => useConversationStore.setState({ streamBuffer: "正在核对报价", turnRunning: true }));
    rerender(<AssistantStream />);
    expect(screen.getByText(/正在核对报价/)).toBeInTheDocument();
  });
});

describe("ToolActivityStrip 细节", () => {
  beforeEach(() => { useToolsStore.getState().reset(); });
  test("无工具调用时不渲染活动条", () => {
    render(<ToolActivityStrip />);
    expect(screen.queryByText(/AGENT 工具活动/)).not.toBeInTheDocument();
  });
  test("按 order 稳定排序渲染每个调用", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "compose_itinerary", args: { days: 90 }, purpose: "排行程" });
    s.applyEvent({ event: "tool_started", call_id: "c2", tool: "profile_field_site", args: { location: "喜洲镇" }, purpose: "" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: true, data: PLAN, degraded: null });
    render(<ToolActivityStrip />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("compose_itinerary");
    expect(rows[1].textContent).toContain("profile_field_site");
    expect(rows[1].textContent).toContain("喜洲镇"); // purpose 为空时回落 args 摘要
  });
  test("配额用尽：每个失败行标记，条下渲染一次性申请 Key 通知卡", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "search_hotels", args: {}, purpose: "住宿" });
    s.applyEvent({ event: "tool_started", call_id: "c2", tool: "search_pois", args: {}, purpose: "景点" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: false, data: null, degraded: null,
      error: { code: "flyai_rate_limited", message: "rate limited", hint: "" } });
    s.applyEvent({ event: "tool_completed", call_id: "c2", ok: false, data: null,
      error: { code: "flyai_rate_limited", message: "rate limited", hint: "" } });
    render(<ToolActivityStrip />);
    expect(screen.getAllByText("每日共享配额已用尽")).toHaveLength(2);        // 每个失败行各一次
    expect(screen.getAllByText(/您也可以申请自己的免费 API Key/)).toHaveLength(1); // 通知卡仅渲染一次
    const link = screen.getByRole("link", { name: /flyai\.open\.fliggy\.com/ });
    expect(link).toHaveAttribute("href", "https://flyai.open.fliggy.com/");
    expect(link).toHaveAttribute("target", "_blank");
  });
  test("普通失败行不触发配额通知卡", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "search_hotels", args: {}, purpose: "住宿" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: false, data: null, degraded: null,
      error: { code: "flyai_error", message: "boom", hint: "" } });
    render(<ToolActivityStrip />);
    expect(screen.queryByText(/每日共享配额已用尽/)).not.toBeInTheDocument();
    expect(screen.queryByText(/您也可以申请自己的免费 API Key/)).not.toBeInTheDocument();
  });
});

describe("Timeline 工具结果卡（同源 tools store）", () => {
  test("空会话显示空状态", () => {
    render(<Timeline />);
    expect(screen.getByText(/尚无对话/)).toBeInTheDocument();
  });
  test("tool_completed 数据经 getToolCard 渲染为结果卡", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "search_hotels", args: {}, purpose: "住宿" });
    s.applyEvent({
      event: "tool_completed", call_id: "c1", ok: true, degraded: null,
      data: { items: [{ name: "大理古城客栈", price: 180, price_raw: "¥180", jump_url: null, pic_url: null, extra: {} }], total: 1, cheapest: 180, dearest: 180 },
    });
    render(<Timeline />);
    expect(screen.getByText("大理古城客栈")).toBeInTheDocument();
    expect(screen.getByText("¥180")).toBeInTheDocument();
  });
  test("运行中的调用不提前渲染结果卡（不误报「未返回数据」）", () => {
    useToolsStore.getState().applyEvent({ event: "tool_started", call_id: "c1", tool: "search_flights", args: {}, purpose: "交通" });
    render(<Timeline />);
    expect(screen.queryByText(/工具未返回数据/)).not.toBeInTheDocument();
  });
});

describe("PhaseBanner", () => {
  beforeEach(() => { useUiStore.getState().reset(); });
  test("显示当前阶段名与说明", () => {
    useProjectStore.getState().loadSnapshot(snapshot({ phase: "itinerary_planning" }));
    render(<PhaseBanner />);
    expect(screen.getByText(/行程规划/)).toBeInTheDocument();
  });
  test("pendingSuggestion 原文出现在横幅，且不自动切 Tab", () => {
    useUiStore.getState().applyEvent({ event: "phase_transition", from: "field_site_profiles", to: "itinerary_planning" });
    render(<PhaseBanner />);
    expect(screen.getByText(/已进入itinerary_planning阶段/)).toBeInTheDocument();
    expect(useUiStore.getState().activeTab).toBe("basic-info");
  });
  test("降级横幅如实转述 code/message/hint，可关闭", () => {
    useUiStore.getState().applyEvent({ event: "error", code: "flyai_unavailable", message: "数据源不可用", hint: "稍后重试" });
    render(<PhaseBanner />);
    expect(screen.getByText(/数据源不可用/)).toBeInTheDocument();
    expect(screen.getByText(/flyai_unavailable/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("关闭"));
    expect(useUiStore.getState().degradedBanner).toBeNull();
  });
});

describe("Composer", () => {
  test("回车发送并清空输入", () => {
    const sent: string[] = [];
    render(<Composer turn={turnHandle({ send: (t: string) => { sent.push(t); } })} />);
    const box = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "帮我排行程" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(sent).toEqual(["帮我排行程"]);
    expect(box.value).toBe("");
  });
  test("Shift+Enter 换行不发送；空白不发送", () => {
    const sent: string[] = [];
    render(<Composer turn={turnHandle({ send: (t: string) => { sent.push(t); } })} />);
    const box = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "第一行" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    fireEvent.change(box, { target: { value: "   " } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(sent).toEqual([]);
  });
  test("busy 显示取消按钮并调用 cancel；conflict 提示已有回合在执行", () => {
    let cancelled = 0;
    render(<Composer turn={turnHandle({ busy: true, conflict: true, cancel: () => { cancelled += 1; } })} />);
    fireEvent.click(screen.getByText("取消"));
    expect(cancelled).toBe(1);
    expect(screen.getByText(/已有回合在执行/)).toBeInTheDocument();
  });
  test("底部注明阶段推进由可验证条件判定", () => {
    render(<Composer turn={turnHandle()} />);
    expect(screen.getByText(/阶段推进由可验证条件判定/)).toBeInTheDocument();
  });
});

describe("右栏面板：BasicInfoPanel / InformantsPanel / KnowledgePanel", () => {
  beforeEach(() => { useUiStore.getState().reset(); });
  test("BasicInfoPanel 编辑器回显快照 research_context 字段（阶段流转史已删）", () => {
    useProjectStore.getState().loadSnapshot(snapshot({
      phase: "itinerary_planning",
      research_context: { discipline: "社会学", budget: "2万" },
    }));
    render(<BasicInfoPanel />);
    expect(screen.getByText(/基本信息（可随时编辑）/)).toBeInTheDocument();
    expect((screen.getByLabelText("学科背景") as HTMLInputElement).value).toBe("社会学");
    expect((screen.getByLabelText("预算") as HTMLInputElement).value).toBe("2万");
    expect(screen.queryByText(/阶段流转/)).not.toBeInTheDocument();
  });
  test("InformantsPanel 无报道人显示空状态", () => {
    render(<InformantsPanel />);
    expect(screen.getByText(/尚无访谈对象/)).toBeInTheDocument();
  });
  test("InformantsPanel 已录入报道人 + suggest_informants 候选同源", () => {
    useProjectStore.getState().loadSnapshot(snapshot({
      informants: [{ code: "P1", type: "村落长老", relation: "董氏宗族 · 经介绍人引荐", contact: "", notes: "伦理：知情同意" }],
    }));
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "suggest_informants", args: {}, purpose: "候选" });
    s.applyEvent({
      event: "tool_completed", call_id: "c1", ok: true, degraded: null,
      data: [{ type: "宗族头人", approach: "经长老引荐", cautions: "避开祭祖期间", question_domains: ["祭祖仪式"], ethics: "录音需授权", access_channel: "村落长老", preparation: "读族谱序言" }],
    });
    render(<InformantsPanel />);
    // 已登记 code「P1」与候选卡编号「P1」（复用 Task 6 InformantCard）分属两节，可同时出现
    expect(screen.getAllByText("P1").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/村落长老/).length).toBeGreaterThan(0);
    expect(screen.getByText(/宗族头人/)).toBeInTheDocument();
    expect(screen.getByText(/祭祖仪式/)).toBeInTheDocument();
  });
  test("KnowledgePanel 研究定位槽位 + 学术纵深静态说明", () => {
    useProjectStore.getState().loadSnapshot(snapshot({
      research_context: { discipline: "社会学", topic: "白族村落宗族组织变迁", scale: "单人", budget: "¥20,000", purpose: "kaiti" },
    }));
    render(<KnowledgePanel />);
    expect(screen.getByText(/社会学/)).toBeInTheDocument();
    expect(screen.getByText(/白族村落宗族组织变迁/)).toBeInTheDocument();
    expect(screen.getByText(/开题报告/)).toBeInTheDocument();
    expect(screen.getByText(/学术纵深/)).toBeInTheDocument();
  });
  test("KnowledgePanel 无快照时如实说明未取得", () => {
    render(<KnowledgePanel />);
    expect(screen.getByText(/尚未取得研究定位/)).toBeInTheDocument();
  });
  test("BudgetPanel 与中栏 ItineraryCard 同源（compose_itinerary.budget）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "compose_itinerary", args: {}, purpose: "排行程" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: true, data: PLAN, degraded: null });
    render(<BudgetPanel />);
    expect(screen.getAllByText("¥17740").length).toBeGreaterThan(0);
    expect(screen.getByText(/住宿（90晚×¥150）/)).toBeInTheDocument();
    expect(screen.getByText(/预算内/)).toBeInTheDocument();
  });
  test("FieldSitesPanel 消费 profile_field_site 的 tool_completed.data（同一份 payload）", () => {
    useProjectStore.getState().loadSnapshot(snapshot({
      phase: "field_site_profiles",
      field_sites: [{ name: "喜洲镇", confirmed: true, profile: null }],
    }));
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "profile_field_site", args: { location: "喜洲镇" }, purpose: "画像" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: true, data: SITE_PROFILE, degraded: null });
    render(<FieldSitesPanel />);
    expect(screen.getByText(/喜洲镇/)).toBeInTheDocument();
    expect(screen.getAllByText("¥620").length).toBeGreaterThan(0);
    expect(screen.getByText(/严家大院/)).toBeInTheDocument();
    expect(screen.getByText(/已确认/)).toBeInTheDocument();
  });
});

describe("RightDock 五面板接线（Tab id = ui store RightTab）", () => {
  beforeEach(() => { useUiStore.getState().reset(); });
  test("默认基本信息面板；点 Tab 渲染对应面板", () => {
    useProjectStore.getState().loadSnapshot(snapshot({
      phase: "itinerary_planning",
      research_context: { discipline: "社会学" },
    }));
    render(<RightDock />);
    expect(screen.getByText(/基本信息（可随时编辑）/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "预算" }));
    expect(useUiStore.getState().activeTab).toBe("budget");
    expect(screen.getByText(/尚未生成行程预算/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "田野点" }));
    expect(screen.getByText(/尚无田野点/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "访谈对象" }));
    expect(screen.getByText(/尚无访谈对象/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "知识库" }));
    expect(screen.getByText(/社会学/)).toBeInTheDocument();
  });
  test("切换建议：稍后=不切 Tab；查看=acceptSuggestion 才切（设计红线）", () => {
    useProjectStore.getState().loadSnapshot(snapshot({ phase: "field_site_profiles" }));
    useUiStore.getState().applyEvent({ event: "phase_transition", from: "research_positioning", to: "field_site_profiles" });
    const { rerender } = render(<RightDock />);
    expect(useUiStore.getState().activeTab).toBe("basic-info"); // 不自动切
    fireEvent.click(screen.getByText("稍后"));
    expect(useUiStore.getState().pendingSuggestion).toBeNull();
    expect(useUiStore.getState().activeTab).toBe("basic-info");

    act(() => useUiStore.getState().applyEvent({ event: "phase_transition", from: "research_positioning", to: "field_site_profiles" }));
    rerender(<RightDock />);
    fireEvent.click(screen.getByText("查看"));
    expect(useUiStore.getState().activeTab).toBe("sites");
    expect(screen.getByText(/尚无田野点|喜洲/)).toBeInTheDocument();
  });
});
