import { describe, expect, test, beforeEach, vi } from "vitest";
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
  test("P1-6：滚动容器带 data-scroll-container，消息有可定位 id", () => {
    useConversationStore.setState({ messages: [
      { role: "user", content: "第一条" },
      { role: "assistant", content: "第二条" },
    ], streamBuffer: "", turnRunning: false, lastUsage: {} });
    const { container } = render(<Timeline />);
    const scroller = container.querySelector('[data-scroll-container="chat"]');
    expect(scroller).not.toBeNull();
    expect(container.querySelector('[data-msg-index="0"]')).not.toBeNull();
    expect(container.querySelector('#msg-1')).not.toBeNull();
    // 自动化入口已挂上（录制脚本直接调用，不靠改 scrollTop）
    expect(typeof (window as unknown as { __scrollToMessage?: unknown }).__scrollToMessage)
      .toBe("function");
  });
  test("P0-10：system_notice 渲染为可关闭提示条，不进消息正文", () => {
    useConversationStore.getState().applyEvent({
      event: "system_notice", code: "max_rounds",
      message: "已达本回合处理上限，回答可能不完整。",
    });
    render(<Timeline />);
    expect(screen.getByText(/已达本回合处理上限/)).toBeInTheDocument();
    // 提示条不属于助手气泡（messages 仍为空）
    expect(useConversationStore.getState().messages).toEqual([]);
    fireEvent.click(screen.getByLabelText("关闭系统提示"));
    expect(screen.queryByText(/已达本回合处理上限/)).not.toBeInTheDocument();
  });
});

describe("ToolActivityStrip", () => {
  test("渲染工具调用状态（中文名，不露英文工具名）", () => {
    useToolsStore.getState().applyEvent({ event: "tool_started", call_id: "c1", tool: "search_hotels", args: {}, purpose: "住宿" });
    render(<ToolActivityStrip />);
    expect(screen.getByText(/酒店搜索/)).toBeInTheDocument();
    expect(screen.queryByText(/search_hotels/)).not.toBeInTheDocument();
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
  test("user 气泡右对齐浅靛蓝洗底（问题3：深底改浅）", () => {
    render(<MessageBubble role="user" content="我从昆明出发" />);
    // 断言用完整 bg-indigowash：bg-indigo 是其子串，含旧类名会误通过
    expect(screen.getByText("我从昆明出发").className).toContain("bg-indigowash");
    expect(screen.getByText("我从昆明出发").className).toContain("text-ink");
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
  test("生成中：头像旋转 + 「罗盘正在生成回答…」状态行（用户反馈：否则像卡死）", () => {
    act(() => useConversationStore.setState({ streamBuffer: "先说结论", turnRunning: true }));
    const { container } = render(<AssistantStream />);
    expect(container.querySelector("img.animate-spin")).not.toBeNull();
    expect(screen.getByText("罗盘正在生成回答…")).toBeInTheDocument();
  });
  test("无正文时提示改用罗盘口吻（助手→罗盘）", () => {
    act(() => useConversationStore.setState({ streamBuffer: "", turnRunning: true }));
    render(<AssistantStream />);
    expect(screen.getByText("罗盘正在组织回复…")).toBeInTheDocument();
    expect(screen.queryByText(/助手正在组织回复/)).not.toBeInTheDocument();
  });
  test("AssistantStream 流式期间剥离 [[options]] 标记（含半截，防闪烁，B 节）", () => {
    act(() => useConversationStore.setState({ streamBuffer: "正在提问[[options]]是|否", turnRunning: true }));
    render(<AssistantStream />);
    expect(screen.getByText(/正在提问/)).toBeInTheDocument();
    expect(screen.queryByText(/\[\[options/)).not.toBeInTheDocument();
  });
  test("MessageBubble 渲染前剥离 [[options]] 标记（历史消息兜底，B 节）", () => {
    render(<MessageBubble role="assistant" content="先校准研究问题吗？[[options]]是|否[[/options]]" />);
    expect(screen.getByText(/先校准研究问题吗？/)).toBeInTheDocument();
    expect(screen.queryByText(/\[\[options/)).not.toBeInTheDocument();
  });
  test("loose list 不产生空行：li 容器无 pre-wrap，段落内软换行保留（B2，生产实录 field_20261018_202833）", () => {
    // react-markdown v10 会把 loose item 块级子元素（p/嵌套列表）之间的换行
    // 保留为 text 节点；li 容器带 whitespace-pre-wrap 时它们渲染成真实空行。
    // 修复：容器去 pre-wrap（块间空白按 inter-element whitespace 折叠），
    // p 自身保留 pre-wrap（段落内软换行仍呈现）。
    const { container } = render(<MessageBubble role="assistant" content={
      "先锁定一下：\n\n1. **哪个支系？** 比如：\n   - 白族银器锻制\n   - 苗银\n   \n2. **为什么关注？"} />);
    const liSpans = container.querySelectorAll("li > span.min-w-0.flex-1");
    expect(liSpans.length).toBeGreaterThanOrEqual(4); // 2 父项 + 2 子项
    for (const span of liSpans) {
      expect(span.className).not.toContain("whitespace-pre-wrap");
    }
    // loose 项内的段落仍保留软换行能力
    for (const p of container.querySelectorAll("li p")) {
      expect(p.className).toContain("whitespace-pre-wrap");
    }
  });
});

describe("全角引号归一（问题3前端，lib/cjkQuotes）", () => {
  test("assistant 含中文的半角引号对渲染为全角，西文对保持半角", () => {
    render(<MessageBubble role="assistant" content={'他说的"银都"作坊集群，源自 "Silver Capital" 的译法'} />);
    expect(screen.getByText(/他说的“银都”作坊集群/)).toBeInTheDocument();
    expect(screen.getByText(/"Silver Capital"/)).toBeInTheDocument();
  });
  test("代码块内的半角引号不动；user 输入不归一", () => {
    render(<MessageBubble role="assistant" content={'示例：\n```\nconst s = "银都"\n```'} />);
    expect(screen.getByText(/const s = "银都"/)).toBeInTheDocument();
    render(<MessageBubble role="user" content={'我要查"银都"的住宿'} />);
    expect(screen.getByText(/我要查"银都"的住宿/)).toBeInTheDocument();
  });
  test("流式缓冲同样归一（AssistantStream）", () => {
    act(() => useConversationStore.setState({ streamBuffer: '这就是"银都"的由来', turnRunning: true }));
    render(<AssistantStream />);
    expect(screen.getByText(/这就是“银都”的由来/)).toBeInTheDocument();
  });
});

describe("ToolActivityStrip 细节", () => {
  beforeEach(() => { useToolsStore.getState().reset(); });
  test("无工具调用时不渲染活动条", () => {
    render(<ToolActivityStrip />);
    expect(screen.queryByText(/AGENT 工具活动/)).not.toBeInTheDocument();
  });
  test("按 order 稳定排序渲染每个调用（保留态：running，中文名）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "compose_itinerary", args: { days: 90 }, purpose: "排行程" });
    s.applyEvent({ event: "tool_started", call_id: "c2", tool: "profile_field_site", args: { location: "喜洲镇" }, purpose: "" });
    render(<ToolActivityStrip />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("行程规划");        // 中文名（不露英文工具名）
    expect(rows[0].textContent).not.toContain("compose_itinerary");
    expect(rows[1].textContent).toContain("田野点画像");
    expect(rows[1].textContent).toContain("喜洲镇"); // purpose 为空时回落 args 摘要
  });
  test("成功且未降级的调用不进活动条（C 节精简：结果由 settled 区卡片承担）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "search_hotels", args: {}, purpose: "住宿" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: true, data: { items: [] }, degraded: null });
    render(<ToolActivityStrip />);
    expect(screen.queryByText(/AGENT 工具活动/)).not.toBeInTheDocument(); // 全部过滤 → 条整体不渲染（不留空壳）
  });
  test("update_research_context 不显示（内部状态写入噪音，含运行与失败）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "update_research_context", args: {}, purpose: "回写" });
    s.applyEvent({ event: "tool_started", call_id: "c2", tool: "update_research_context", args: {}, purpose: "" });
    s.applyEvent({ event: "tool_completed", call_id: "c2", ok: false, data: null, degraded: null,
      error: { code: "internal_error", message: "boom", hint: "" } });
    render(<ToolActivityStrip />);
    expect(screen.queryByText(/update_research_context/)).not.toBeInTheDocument();
    expect(screen.queryByText(/AGENT 工具活动/)).not.toBeInTheDocument();
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
  test("同一工具连续多次失败聚合为一行（中文名 + 连续 N 次，不露英文名/args）", () => {
    const s = useToolsStore.getState();
    for (const id of ["c1", "c2", "c3"]) {
      s.applyEvent({ event: "tool_started", call_id: id, tool: "suggest_informants",
        args: { research_topic: "白族银器", site_profile: "{...}" }, purpose: "生成候选" });
      s.applyEvent({ event: "tool_completed", call_id: id, ok: false, data: null, degraded: null,
        error: { code: "invalid_arguments", message: "参数校验失败", hint: "" } });
    }
    render(<ToolActivityStrip />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(1);                              // 3 次失败合并为 1 行
    expect(rows[0].textContent).toContain("访谈对象候选生成"); // 中文名
    expect(rows[0].textContent).toContain("连续 3 次失败");
    expect(rows[0].textContent).not.toContain("suggest_informants"); // 不露英文工具名
    expect(rows[0].textContent).not.toContain("白族银器");           // 不展开 args
  });
  test("不同工具的失败不聚合（各自一行）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "suggest_informants", args: {}, purpose: "" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: false, data: null, degraded: null,
      error: { code: "invalid_arguments", message: "boom1", hint: "" } });
    s.applyEvent({ event: "tool_started", call_id: "c2", tool: "compose_proposal", args: {}, purpose: "" });
    s.applyEvent({ event: "tool_completed", call_id: "c2", ok: false, data: null, degraded: null,
      error: { code: "invalid_arguments", message: "boom2", hint: "" } });
    render(<ToolActivityStrip />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText(/访谈对象候选生成/)).toBeInTheDocument();
    expect(screen.getByText(/研究方案草稿生成/)).toBeInTheDocument();
  });
});

describe("Timeline 工具结果卡（同源 tools store）", () => {
  test("空会话（对话史已回填）显示空状态", () => {
    useConversationStore.getState().hydrate([]);   // 模拟 GET /messages 返回空史的回填完毕态
    render(<Timeline />);
    expect(screen.getByText(/尚无对话/)).toBeInTheDocument();
  });
  test("对话史回填中显示「加载中」+ 旋转 logo，不误显「尚无对话」（问题2）", () => {
    render(<Timeline />);   // beforeEach reset 后 hydrated=false，即进入工作台的加载窗口
    expect(screen.getByText(/加载中/)).toBeInTheDocument();
    expect(screen.queryByText(/尚无对话/)).not.toBeInTheDocument();
    expect(document.querySelector("img.animate-spin")).not.toBeNull();  // 不停旋转的 logo
  });
  test("collapsed 级工具移出对话时间线（Task 10 中间态，证据 tab 由 Task 12 接线）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "search_hotels", args: {}, purpose: "住宿" });
    s.applyEvent({
      event: "tool_completed", call_id: "c1", ok: true, degraded: null,
      data: { items: [{ name: "大理古城客栈", price: 180, price_raw: "¥180", jump_url: null, pic_url: null, extra: {} }], total: 1, cheapest: 180, dearest: 180 },
    });
    render(<Timeline />);
    expect(screen.queryByTestId("collapsed-tool-card")).not.toBeInTheDocument(); // 折叠条移出对话时间线（spec C4）
    expect(screen.queryByText("大理古城客栈")).not.toBeInTheDocument();          // 原始结果不露出（证据 tab 接线前的过渡态）
  });
  test("full 级工具原样整卡直渲（compose_itinerary 结果卡不折叠）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "compose_itinerary", args: {}, purpose: "排行程" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: true, data: PLAN, degraded: null });
    render(<Timeline />);
    expect(screen.queryByTestId("collapsed-tool-card")).not.toBeInTheDocument();
    expect(screen.getByText(/昆明 → 喜洲镇/)).toBeInTheDocument(); // ItineraryCard 原卡路线
  });
  test("hidden 级（update_research_context）成功与失败均不进时间线", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "update_research_context", args: {}, purpose: "" });
    s.applyEvent({ event: "tool_started", call_id: "c2", tool: "update_research_context", args: {}, purpose: "" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: true, data: null, degraded: null });
    s.applyEvent({ event: "tool_completed", call_id: "c2", ok: false, data: null, degraded: null,
      error: { code: "internal_error", message: "boom", hint: "" } });
    render(<Timeline />);
    expect(screen.queryByText(/update_research_context/)).not.toBeInTheDocument();
    expect(screen.queryByText(/boom/)).not.toBeInTheDocument(); // 失败也不显示（正文自会解释）
  });
  test("hidden 级（get_project_brief）不进时间线：不出「未适配的结构化视图」兑底卡（问题②）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "get_project_brief", args: {}, purpose: "" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: true,
      data: { brief: "项目摘要：…" }, degraded: null });
    render(<Timeline />);
    expect(screen.queryByText(/未适配的结构化视图/)).not.toBeInTheDocument();
    expect(screen.queryByText(/get_project_brief/)).not.toBeInTheDocument();
    expect(screen.queryByText(/项目摘要/)).not.toBeInTheDocument();
  });
  test("运行中的调用不提前渲染结果卡（不误报「未返回数据」）", () => {
    useToolsStore.getState().applyEvent({ event: "tool_started", call_id: "c1", tool: "search_flights", args: {}, purpose: "交通" });
    render(<Timeline />);
    expect(screen.queryByText(/工具未返回数据/)).not.toBeInTheDocument();
  });
  test("田野点画像不进对话流（右栏面板承担，2026-10-20 用户反馈）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "profile_field_site", args: { location: "喜洲镇" }, purpose: "画像" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: true, data: SITE_PROFILE, degraded: null });
    render(<Timeline />);
    expect(screen.queryByText("田野点画像")).not.toBeInTheDocument();
    expect(screen.queryByText(/严家大院/)).not.toBeInTheDocument();
    expect(screen.queryByText(/未适配的结构化视图/)).not.toBeInTheDocument();
  });
  test("失败的 full 级工具不渲染结果卡（访谈对象候选窗不滞留对话流）", () => {
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c1", tool: "suggest_informants", args: {}, purpose: "生成候选" });
    s.applyEvent({ event: "tool_completed", call_id: "c1", ok: false, data: null, degraded: null,
      error: { code: "invalid_arguments", message: "参数校验失败", hint: "" } });
    render(<Timeline />);
    // 失败结果由活动条与助手正文承担，不再渲染空卡（NoData 占位）
    expect(screen.queryByText(/未取得访谈对象候选/)).not.toBeInTheDocument();
  });
});

describe("PhaseBanner", () => {
  beforeEach(() => { useUiStore.getState().reset(); });
  test("显示当前阶段名与说明", () => {
    useProjectStore.getState().loadSnapshot(snapshot({ phase: "itinerary_planning" }));
    render(<PhaseBanner />);
    expect(screen.getByText(/行程规划/)).toBeInTheDocument();
  });
  test("阶段推进：横幅只显阶段与解锁说明，右栏直接切到该阶段面板", () => {
    useProjectStore.getState().loadSnapshot(snapshot({ phase: "itinerary_planning" }));
    useUiStore.getState().applyEvent({ event: "phase_transition", from: "field_site_profiles", to: "itinerary_planning" });
    render(<PhaseBanner />);
    expect(screen.getByText(/行程规划/)).toBeInTheDocument();
    expect(screen.queryByText(/建议切换到/)).not.toBeInTheDocument();
    expect(useUiStore.getState().activeTab).toBe("budget");   // 自动切，无需手点
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
  test("匿名会话底注为注册提示（匿名记录离开即清除），已登录时不渲染", () => {
    const onRegister = vi.fn();
    const { unmount } = render(<Composer turn={turnHandle()} guest onRegister={onRegister} />);
    expect(screen.getByText(/未注册的对话记录会在离开本页时清除/)).toBeInTheDocument();
    // 旧底注已下线
    expect(screen.queryByText(/所需信息齐备后，才会进入下一阶段/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("注册 / 登录"));
    expect(onRegister).toHaveBeenCalledTimes(1);

    unmount();
    render(<Composer turn={turnHandle()} />);   // 已登录（guest 默认 false）
    expect(screen.queryByText(/未注册的对话记录/)).not.toBeInTheDocument();
  });
  test("点击输入壳空白聚焦 textarea（问题4：点空白无反应）", () => {
    render(<Composer turn={turnHandle()} />);
    const shell = screen.getByRole("textbox").closest("div"); // 输入壳（textarea 直接父级）
    fireEvent.click(shell!);
    expect(document.activeElement).toBe(screen.getByRole("textbox"));
  });
  test("快捷作答 chips（单组无标签）：点选即发送、其他（在下方输入）聚焦输入框", () => {
    const sent: string[] = [];
    useConversationStore.setState({ quickReplyGroups: [{ label: "", options: ["是", "换个方向"] }] });
    render(<Composer turn={turnHandle({ send: (t: string) => { sent.push(t); } })} />);
    fireEvent.click(screen.getByRole("button", { name: "是" }));
    expect(sent).toEqual(["是"]); // 单组无标签：直调 send，不经输入框
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "其他（在下方输入）" }));
    expect(document.activeElement).toBe(screen.getByRole("textbox")); // 转入打字
  });
  test("多组带标签：逐组选择 + 发送拼成多行一条消息（问题①）", () => {
    const sent: string[] = [];
    useConversationStore.setState({ quickReplyGroups: [
      { label: "用途", options: ["高校开题", "基金申报"] },
      { label: "学科", options: ["人类学", "社会学"] },
    ] });
    render(<Composer turn={turnHandle({ send: (t: string) => { sent.push(t); } })} />);
    // 未选时发送禁用
    expect((screen.getByTestId("quick-reply-send") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "高校开题" }));
    fireEvent.click(screen.getByRole("button", { name: "人类学" }));
    fireEvent.click(screen.getByTestId("quick-reply-send"));
    expect(sent).toEqual(["用途：高校开题\n学科：人类学"]); // 两组答案合成一条
  });
  test("多组：允许只选部分组发送；点已选 chip 可取消选择", () => {
    const sent: string[] = [];
    useConversationStore.setState({ quickReplyGroups: [
      { label: "用途", options: ["开题", "申报"] },
      { label: "学科", options: ["人类学", "社会学"] },
    ] });
    render(<Composer turn={turnHandle({ send: (t: string) => { sent.push(t); } })} />);
    fireEvent.click(screen.getByRole("button", { name: "开题" }));
    fireEvent.click(screen.getByTestId("quick-reply-send"));
    expect(sent).toEqual(["用途：开题"]);           // 只选一组也能发
    fireEvent.click(screen.getByRole("button", { name: "社会学" }));
    fireEvent.click(screen.getByRole("button", { name: "社会学" })); // 再点取消
    fireEvent.click(screen.getByTestId("quick-reply-send"));
    expect(sent).toEqual(["用途：开题", "用途：开题"]); // 学科未选，仍只发用途
  });
  test("quickReplyGroups 为空或 busy 时不渲染 chips（渐进增强）", () => {
    const { rerender } = render(<Composer turn={turnHandle()} />);
    expect(screen.queryByTestId("quick-replies")).not.toBeInTheDocument(); // 无选项不出现
    useConversationStore.setState({ quickReplyGroups: [{ label: "", options: ["是"] }] });
    rerender(<Composer turn={turnHandle({ busy: true })} />);
    expect(screen.queryByTestId("quick-replies")).not.toBeInTheDocument(); // 回合运行中退场
    rerender(<Composer turn={turnHandle()} />);
    expect(screen.getByTestId("quick-replies")).toBeInTheDocument();
  });
});

describe("右栏面板：BasicInfoPanel / InformantsPanel / KnowledgePanel", () => {
  beforeEach(() => { useUiStore.getState().reset(); });
  // 任务2 后 KnowledgePanel 挂载即 GET /knowledge（loadSnapshot 已设 projectId），
  // 相关测试需 stub fetch；此 describe 统一清理，不影响文件内其余测试
  afterEach(() => { vi.unstubAllGlobals(); });
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
  test("BasicInfoPanel 单栏布局（任务3）：grid-cols-1 + AutoGrowTextarea", () => {
    useProjectStore.getState().loadSnapshot(snapshot({
      research_context: { research_questions: "一、宗族组织如何维系\n二、仪式角色的分工\n三、外迁人群的认同" },
    }));
    const { container } = render(<BasicInfoPanel />);
    // 双栏改单栏：槽位编辑区一格一行（拖宽右栏时长文本不再被压成半宽）
    const grid = container.querySelector<HTMLElement>(".grid");
    expect(grid).not.toBeNull();
    expect(grid!.className).toContain("grid-cols-1");
    expect(grid!.className).not.toContain("grid-cols-2");
    // AutoGrowTextarea：rows=1 起步 + 禁用手把（高度由 value 变化/宽度重算撑起；
    // jsdom 无布局与 ResizeObserver，守卫跳过，断言只覆盖静态属性）
    const ta = screen.getByLabelText("研究问题") as HTMLTextAreaElement;
    expect(ta.rows).toBe(1);
    expect(ta.className).toContain("resize-none");
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
  test("KnowledgePanel 槽位投影已删——只留已存条目区（任务2）", async () => {
    // 2026-10-18 槽位投影整体删除：槽位卡/「已填 N / 11 槽位」计数/extras chips/
    // 「尚未取得研究定位」空态移除，编辑入口收敛到基本信息面板；知识库只呈现
    // agent 存入的材料条目。挂载即 GET /knowledge（projectId 已设）：stub 空列表。
    vi.stubGlobal("fetch", vi.fn(async () =>
      ({ ok: true, status: 200, json: async () => [], text: async () => "[]" } as unknown as Response)));
    useProjectStore.getState().loadSnapshot(snapshot({
      research_context: { discipline: "社会学", topic: "白族村落宗族组织变迁", scale: "单人", budget: "¥20,000", purpose: "kaiti" },
    }));
    render(<KnowledgePanel />);
    // research_context 槽位值不再投影到知识库
    expect(screen.queryByText(/社会学/)).not.toBeInTheDocument();
    expect(screen.queryByText(/白族村落宗族组织变迁/)).not.toBeInTheDocument();
    expect(screen.queryByText(/开题报告/)).not.toBeInTheDocument();
    expect(screen.queryByText(/已填/)).not.toBeInTheDocument();
    // 已存条目区保留：空列表 → 计数 0 + 空态文案
    expect(await screen.findByText(/已存条目 · 0/)).toBeInTheDocument();
    expect(screen.getByText(/尚未存入知识条目/)).toBeInTheDocument();
  });
  test("KnowledgePanel 无项目时不发知识请求（条目区呈占位计数）", () => {
    // store reset 后 projectId=null：不拉取（不发无意义请求），计数呈占位「…」
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<KnowledgePanel />);
    expect(screen.getByText(/已存条目 · …/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
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
  test("右栏补齐原中栏独占的区块：设施示意/内核建议/住宿比较/空间检索", async () => {
    useProjectStore.getState().loadSnapshot(snapshot({
      phase: "field_site_profiles",
      field_sites: [{ name: "喜洲镇", confirmed: true, profile: null }],
    }));
    const rich = {
      location: "喜洲镇",
      geo: { location: "100.196,25.047", province: "云南省" },
      facilities: { items: [{ name: "喜洲卫生院", extra: { location: "100.20,25.05", facility_kind: "卫生院" } }], total: 1 },
      transport: null, accommodation: null, culture: [], amenities: null,
      summary: {
        section_counts: {}, transport_min: null, accommodation_nightly_min: null,
        hints: { accommodation: "平台未见住宿供给：建议以最近枢纽城市住宿为基地" },
      },
      lodging_comparison: [{ name: "近舍", cost_estimate: 864, commute_minutes_per_visit_day: 20,
        constraints: { status: "unknown", missing_fields: ["连续房态"] } }],
      spatial_results: [{ query: { label: "生产场景", research_purpose: "了解学习过程",
        keywords: ["作坊"], administrative_scope: "喜洲镇" },
        items: [{ name: "甲作坊" }], stop_reason: "候选已足够", evidence_boundary: "进入条件待核实" }],
    };
    const s = useToolsStore.getState();
    s.applyEvent({ event: "tool_started", call_id: "c9", tool: "profile_field_site", args: { location: "喜洲镇" }, purpose: "画像" });
    s.applyEvent({ event: "tool_completed", call_id: "c9", ok: true, data: rich, degraded: null });
    // 地图块会发 settings 请求（无桩 → 降级），await act 冲干净异步更新
    await act(async () => { render(<FieldSitesPanel />); });
    expect(screen.getByText("周边设施示意")).toBeInTheDocument();   // 设施坐标点位
    expect(screen.getByText("喜洲卫生院")).toBeInTheDocument();
    expect(screen.getByText(/平台未见住宿供给/)).toBeInTheDocument(); // 内核二段式建议
    expect(screen.getByText(/住宿与通勤比较/)).toBeInTheDocument();
    expect(screen.getByText(/房费与通勤合计情景估算/)).toBeInTheDocument();
    expect(screen.getByText(/候选观察场所/)).toBeInTheDocument();
    expect(screen.getByText("甲作坊")).toBeInTheDocument();
    expect(screen.getByText("高德底座")).toBeInTheDocument();       // 来源徽标
  });
});

describe("RightDock 五面板接线（Tab id = ui store RightTab）", () => {
  beforeEach(() => { useUiStore.getState().reset(); });
  // 任务2 后切知识库 Tab 即挂载 KnowledgePanel → GET /knowledge（loadSnapshot 已设 projectId），相关测试需 stub；统一清理
  afterEach(() => { vi.unstubAllGlobals(); });
  test("默认基本信息面板；点 Tab 渲染对应面板", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      ({ ok: true, status: 200, json: async () => [], text: async () => "[]" } as unknown as Response)));
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
    // 任务2：槽位投影已删——知识库面板只剩已存条目区，discipline 不再渲染
    expect(await screen.findByText(/已存条目 · 0/)).toBeInTheDocument();
    expect(screen.queryByText(/社会学/)).not.toBeInTheDocument();
  });
  test("阶段推进 → 右栏直接切到对应面板（不再弹「查看/稍后」toast）", () => {
    useProjectStore.getState().loadSnapshot(snapshot({ phase: "field_site_profiles" }));
    useUiStore.getState().applyEvent({ event: "phase_transition", from: "research_positioning", to: "field_site_profiles" });
    render(<RightDock />);
    expect(useUiStore.getState().activeTab).toBe("sites");
    expect(screen.queryByText("稍后")).not.toBeInTheDocument();
    expect(screen.getByText(/尚无田野点|喜洲/)).toBeInTheDocument();
  });
});


// ------------------------------- Timeline 重新生成（Task 11）

describe("Timeline — 重新生成", () => {
  test("中断提示 + 按钮点击回调", () => {
    const onRetry = vi.fn();
    render(<Timeline onRetry={onRetry} canRetry incomplete />);
    expect(screen.getByText(/本轮生成中断/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test("失败态（canRetry 单独成立）：显示本轮生成失败，无中断措辞（问题1）", () => {
    const onRetry = vi.fn();
    render(<Timeline onRetry={onRetry} canRetry />);
    expect(screen.getByText("本轮生成失败。")).toBeInTheDocument();
    expect(screen.queryByText(/本轮生成中断/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test("无中断无按钮", () => {
    render(<Timeline />);
    expect(screen.queryByRole("button", { name: "重新生成" })).not.toBeInTheDocument();
    expect(screen.queryByText(/本轮生成中断/)).not.toBeInTheDocument();
  });
});

