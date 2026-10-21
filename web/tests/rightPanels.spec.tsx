// web/tests/rightPanels.spec.tsx —— 右栏确认/采纳/研究信息编辑（Task 9）
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { InformantCard } from "../src/components/cards/InformantCard";
import { InformantsPanel } from "../src/components/right/InformantsPanel";
import { FieldSitesPanel } from "../src/components/right/FieldSitesPanel";
import { KnowledgePanel } from "../src/components/right/KnowledgePanel";
import { BasicInfoPanel } from "../src/components/right/BasicInfoPanel";
import { useProjectStore } from "../src/stores/project";
import { useToolsStore } from "../src/stores/tools";
import type { ProjectSnapshot } from "../src/types/models";

/** R06b：可指定 ok/status 的响应桩（stubFetch 的失败态版本；末项耗尽后重复）。 */
function stubFetchSeq(resps: Array<{ body: unknown; ok?: boolean; status?: number }>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let i = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const r = resps[Math.min(i++, resps.length - 1)] ?? { body: {} };
    return { ok: r.ok ?? true, status: r.status ?? 200, json: async () => r.body } as unknown as Response;
  }));
  return calls;
}

function stubFetch(resps: unknown[] = []) {
  return stubFetchSeq(resps.map((body) => ({ body })));
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

test("学术背景编辑和撤回：清空导师显式上行，保留院校与田野地点", async () => {
  useProjectStore.getState().loadSnapshot({ ...SNAP, research_context: {
    target_advisor: "陈老师", target_institution: "中大", target_location: "福建",
  } });
  const calls = stubFetch([
    { research_context: { target_advisor: "", target_institution: "中大", target_location: "福建" } },
    { ...SNAP, research_context: { target_advisor: "", target_institution: "中大", target_location: "福建" } },
  ]);
  render(<BasicInfoPanel />);
  expect(screen.getByLabelText("目标院校")).toHaveValue("中大");
  expect(screen.getByLabelText("目标地点")).toHaveValue("福建");
  fireEvent.change(screen.getByLabelText("目标导师"), { target: { value: "" } });
  fireEvent.click(screen.getByText("保存修正"));
  await waitFor(() => expect(calls.length).toBeGreaterThan(0));
  const patch = JSON.parse(String(calls[0].init?.body));
  expect(patch.clear_fields).toEqual(["target_advisor"]);
  expect(patch).not.toHaveProperty("target_advisor");
  expect(patch.target_institution).toBe("中大");
  expect(patch.target_location).toBe("福建");
  await waitFor(() => expect(useProjectStore.getState().snapshot?.research_context.target_advisor).toBe(""));
});

test("编辑期间刷新增加学术字段，不因未触碰的空草稿撤回新值", async () => {
  useProjectStore.getState().loadSnapshot({ ...SNAP, research_context: { budget: "1万" } });
  const calls = stubFetch([
    { research_context: { budget: "2万", target_advisor: "陈老师" } },
    { ...SNAP, research_context: { budget: "2万", target_advisor: "陈老师" } },
  ]);
  const { rerender } = render(<BasicInfoPanel />);
  fireEvent.change(screen.getByLabelText("预算"), { target: { value: "2万" } });
  useProjectStore.getState().loadSnapshot({ ...SNAP, research_context: {
    budget: "1万", target_advisor: "陈老师",
  } });
  rerender(<BasicInfoPanel />);
  fireEvent.click(screen.getByText("保存修正"));
  await waitFor(() => expect(calls.length).toBeGreaterThan(0));
  const patch = JSON.parse(String(calls[0].init?.body));
  expect(patch).not.toHaveProperty("clear_fields");
  expect(patch).not.toHaveProperty("target_advisor");
  await waitFor(() => expect(useProjectStore.getState().snapshot?.research_context.target_advisor).toBe("陈老师"));
});

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

  test("失败的 suggest_informants 不渲染候选卡（右栏回到空态）", () => {
    useToolsStore.setState({
      byCallId: { c1: { call_id: "c1", tool: "suggest_informants", purpose: "", status: "failed", args: {},
                        data: null, degraded: null,
                        error: { code: "invalid_arguments", message: "参数校验失败", hint: "" } } },
      order: ["c1"],
    });
    render(<InformantsPanel />);
    expect(screen.queryByText(/未取得访谈对象候选/)).not.toBeInTheDocument();
    expect(screen.getByText(/尚无访谈对象/)).toBeInTheDocument();
  });

  test("done 但零候选的 suggest_informants 不渲染候选卡（空数据不滞留）", () => {
    useToolsStore.setState({
      byCallId: { c1: { call_id: "c1", tool: "suggest_informants", purpose: "", status: "done", args: {},
                        data: [], degraded: null } },
      order: ["c1"],
    });
    render(<InformantsPanel />);
    expect(screen.queryByText(/未取得访谈对象候选/)).not.toBeInTheDocument();
    expect(screen.getByText(/尚无访谈对象/)).toBeInTheDocument();
  });

  test("P2-1：已登记的 type 置灰为「已登记」，不再可点", () => {
    useProjectStore.getState().loadSnapshot({
      ...SNAP,
      informants: [{ id: "inf_1", code: "INF-01", type: "宗族长老",
                     relation: "", contact: "", notes: "" }],
    });
    useToolsStore.setState({
      byCallId: { c1: { call_id: "c1", tool: "suggest_informants", purpose: "", status: "done", args: {},
                        data: [{ type: "宗族长老" }], degraded: null } },
      order: ["c1"],
    });
    render(<InformantsPanel />);
    const btn = screen.getByText("已登记");
    expect(btn).toBeDisabled();
    expect(screen.getByText(/候选 · 已登记/)).toBeInTheDocument();
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

describe("BasicInfoPanel 研究信息编辑", () => {
  test("改预算并保存 → PUT research-context 只带非空字段", async () => {
    const calls = stubFetch([
      { research_context: { budget: "5万" }, phase_transition: null },
      { ...SNAP, research_context: { budget: "5万" } },
    ]);
    render(<BasicInfoPanel />);
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
    render(<BasicInfoPanel />);
    fireEvent.click(screen.getByText("保存修正"));
    await waitFor(() =>
      expect(screen.getByText(/未填写任何要修正的字段/)).toBeInTheDocument());
    expect(calls).toHaveLength(0);
  });

  test("N03：研究问题字段可编辑并随保存上行（旧后端 ContextPatch 缺此字段致 422）", async () => {
    useProjectStore.getState().loadSnapshot({ ...SNAP, research_context: {} });
    const calls = stubFetch([
      { research_context: { research_questions: "RQ1" }, phase_transition: null },
      { ...SNAP, research_context: { research_questions: "RQ1" } },
    ]);
    render(<BasicInfoPanel />);
    fireEvent.change(screen.getByLabelText("研究问题"), { target: { value: "RQ1" } });
    fireEvent.click(screen.getByText("保存修正"));
    await waitFor(() =>
      expect(calls[0].url).toBe("/projects/p1/research-context"));
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ research_questions: "RQ1" });
  });

  test("N03：取消勾选不适用访谈对象 → PUT 明确发 interviews_not_applicable:false 撤销", async () => {
    useProjectStore.getState().loadSnapshot({
      ...SNAP,
      research_context: { interviews_not_applicable: true, interviews_na_reason: "纯档案研究" },
    });
    const calls = stubFetch([
      { research_context: { interviews_not_applicable: false }, phase_transition: null },
      { ...SNAP, research_context: { interviews_not_applicable: false } },
    ]);
    render(<BasicInfoPanel />);
    // 当前为勾选态（快照 NA=true），点标签取消勾选
    fireEvent.click(screen.getByText(/不适用访谈对象/));
    fireEvent.click(screen.getByText("保存修正"));
    await waitFor(() =>
      expect(calls[0].url).toBe("/projects/p1/research-context"));
    // 旧 if(na) 分支不会发 false；N03 修复后明确上行 false 以撤销已存 NA
    expect(JSON.parse(String(calls[0].init?.body)).interviews_not_applicable).toBe(false);
  });

  test("P4：长文本字段用多行 textarea 完整呈现（换行保留，不再单行截断）", () => {
    useProjectStore.getState().loadSnapshot({
      ...SNAP,
      research_context: { topic: "白族村落宗族组织变迁：\n以喜洲镇为例的宗族与祭祖仪式考察" },
    });
    render(<BasicInfoPanel />);
    const topic = screen.getByLabelText("研究主题");
    expect(topic.tagName).toBe("TEXTAREA"); // 单行 input → 多行 textarea
    expect((topic as HTMLTextAreaElement).value).toBe(
      "白族村落宗族组织变迁：\n以喜洲镇为例的宗族与祭祖仪式考察"); // input 的 value 会剥离换行

    // 不适用原因同为面板输入，一并多行化（勾选后出现）
    fireEvent.click(screen.getByText(/不适用访谈对象/));
    expect(screen.getByLabelText("不适用原因").tagName).toBe("TEXTAREA");
  });
});

describe("FieldSitesPanel — R06b 回读失败提示与重试", () => {
  const MSG = "快照刷新失败，最新田野点/行程可能未显示";

  test("refreshError 非空 → 提示条可见，站点列表照常渲染", () => {
    useProjectStore.getState().setRefreshError(MSG);
    render(<FieldSitesPanel />);
    expect(screen.getByText(MSG)).toBeInTheDocument();
    expect(screen.getByText("田野点 · 1")).toBeInTheDocument();   // 列表不被遮挡
    expect(screen.getByText("确认")).toBeInTheDocument();        // site_1 未确认，按钮仍在
  });

  test("点击重试 → GET /projects/p1，成功后提示消失、快照更新", async () => {
    useProjectStore.getState().setRefreshError(MSG);
    const calls = stubFetchSeq([
      { body: { ...SNAP, field_sites: [{ ...SNAP.field_sites[0], confirmed: true }] } },
    ]);
    render(<FieldSitesPanel />);
    fireEvent.click(screen.getByText("重试"));
    await waitFor(() => expect(calls[0]?.url).toBe("/projects/p1"));
    await waitFor(() => expect(useProjectStore.getState().refreshError).toBeNull());
    expect(screen.queryByText(MSG)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(useProjectStore.getState().snapshot?.field_sites[0].confirmed).toBe(true));
  });

  test("重试仍失败（500）→ 提示保留，仍可再试", async () => {
    useProjectStore.getState().setRefreshError(MSG);
    const calls = stubFetchSeq([{ body: { detail: "boom" }, ok: false, status: 500 }]);
    render(<FieldSitesPanel />);
    fireEvent.click(screen.getByText("重试"));
    await waitFor(() => expect(calls[0]?.url).toBe("/projects/p1"));
    await waitFor(() => expect(useProjectStore.getState().refreshError).toBe(MSG));
    expect(screen.getByText(MSG)).toBeInTheDocument();
    expect(screen.getByText("重试")).toBeInTheDocument();
  });

  test("项目无田野点 + refreshError → 空态下提示条仍可见且重试可点（终审 Minor #1）", async () => {
    useProjectStore.getState().loadSnapshot({ ...SNAP, field_sites: [] });
    useProjectStore.getState().setRefreshError(MSG);
    const calls = stubFetchSeq([{ body: { ...SNAP, field_sites: [] } }]);
    render(<FieldSitesPanel />);
    expect(screen.getByText("尚无田野点")).toBeInTheDocument();   // 空态本体照常渲染
    expect(screen.getByText(MSG)).toBeInTheDocument();           // 提示条不被空态早返回吞掉
    fireEvent.click(screen.getByText("重试"));                    // 重试入口可点
    await waitFor(() => expect(calls[0]?.url).toBe("/projects/p1"));
    await waitFor(() => expect(useProjectStore.getState().refreshError).toBeNull());
  });
});

describe("KnowledgePanel 知识条目（审计三 Fix 1c + P1 折叠/编辑）", () => {
  test("挂载即 GET /knowledge；条目默认折叠，点击标题才显示正文", async () => {
    const calls = stubFetch([
      [{ id: "k1", title: "印度签证准入", content: "电子签 30 天", category: "general" },
       { id: "k2", title: "节庆日历", content: "排灯节 11 月", category: "general" }],
    ]);
    render(<KnowledgePanel />);
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/projects/p1/knowledge")).toBe(true));
    expect(await screen.findByText("印度签证准入")).toBeInTheDocument();
    expect(screen.queryByText("电子签 30 天")).not.toBeInTheDocument(); // 折叠：正文默认不可见
    expect(screen.getByText("节庆日历")).toBeInTheDocument();
    fireEvent.click(screen.getByText("印度签证准入"));                   // 点击标题展开
    expect(await screen.findByText("电子签 30 天")).toBeInTheDocument();
  });

  test("展开后按 Markdown 渲染正文（加粗 strong + 单换行）", async () => {
    stubFetch([
      [{ id: "k1", title: "印度签证准入", content: "**电子签** 30 天\n需提前申请", category: "general" }],
    ]);
    const { container } = render(<KnowledgePanel />);
    fireEvent.click(await screen.findByText("印度签证准入"));
    await waitFor(() =>
      expect(container.querySelector("strong")?.textContent).toBe("电子签"));
    expect(container.querySelectorAll("br").length).toBeGreaterThanOrEqual(1); // 单换行不破段
  });

  test("展开后编辑并保存 → PUT 条目端点，界面同步新值", async () => {
    const calls = stubFetch([
      [{ id: "k1", title: "印度签证准入", content: "电子签 30 天", category: "general" }],
      { id: "k1", title: "签证材料", content: "电子签 30 天\n加急 2 天", category: "general" },
    ]);
    render(<KnowledgePanel />);
    fireEvent.click(await screen.findByText("印度签证准入"));
    fireEvent.click(screen.getByText("编辑"));
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "签证材料" } });
    fireEvent.change(screen.getByLabelText("内容"), { target: { value: "电子签 30 天\n加急 2 天" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => expect(calls[1]?.url).toBe("/projects/p1/knowledge/k1"));
    expect(calls[1]?.init?.method).toBe("PUT");
    expect(JSON.parse(String(calls[1]?.init?.body))).toEqual(
      { title: "签证材料", content: "电子签 30 天\n加急 2 天" });
    expect(await screen.findByText("签证材料")).toBeInTheDocument(); // 保存后回到展示态且更新
  });

  test("空知识库 → 如实提示无条目，不伪造", async () => {
    stubFetch([[]]);
    render(<KnowledgePanel />);
    expect(await screen.findByText(/尚未存入知识条目/)).toBeInTheDocument();
  });

  test("端点失败 → 如实提示加载失败，不崩", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      ({ ok: false, status: 500, text: async () => "boom" } as unknown as Response)));
    render(<KnowledgePanel />);
    expect(await screen.findByText(/知识条目加载失败/)).toBeInTheDocument();
  });

  test("add_knowledge_entry 工具完成后重拉（回合内落盘立即可见）", async () => {
    const calls = stubFetch([
      [],
      [{ id: "k9", title: "新条目", content: "正文", category: "general" }],
    ]);
    render(<KnowledgePanel />);
    await waitFor(() => expect(calls).toHaveLength(1));
    useToolsStore.setState({
      byCallId: { c9: { call_id: "c9", tool: "add_knowledge_entry", purpose: "", status: "done",
                        args: {}, ok: true, data: { id: "k9" }, degraded: null } },
      order: ["c9"],
    });
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(await screen.findByText("新条目")).toBeInTheDocument();
  });
});
