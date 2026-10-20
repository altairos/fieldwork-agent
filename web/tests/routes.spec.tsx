import { describe, expect, test, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ProjectHub } from "../src/routes/ProjectHub";
import { ProposalView } from "../src/routes/ProposalView";
import { ProposalViewer } from "../src/components/proposal/ProposalViewer";
import { ChapterNav } from "../src/components/proposal/ChapterNav";
import { saveKeys } from "../src/lib/keys";
import * as rest from "../src/api/rest";
import { useExportFormatStore } from "../src/stores/exportFormat";
import { useAuthStore } from "../src/stores/auth";

vi.mock("../src/api/rest");

beforeEach(() => {
  vi.clearAllMocks();
  // ApiKeyCard 挂载即查 key 状态与健康探询：模块 mock 默认返 undefined 会崩 .then，补默认值
  vi.mocked(rest.rest.settingsStatus).mockResolvedValue({ dashscope_configured: true, flyai_configured: true, amap_configured: true, amap_js_configured: true, amap_js_key: "stub-js-key", amap_js_security: "stub-js-security" });
  vi.mocked(rest.rest.health).mockResolvedValue({ status: "ok", auth_required: false });
  useAuthStore.setState({ user: null });
});

// ──────────────────────────────────────────────────────────────────────────
// 首页极简重设计：视觉中央一个输入框 + 示例 chips + 登录弹层入口；
// 项目列表收进「继续先前项目」折叠入口；学科/模板/侧栏说明等冗余区块移除。
// ──────────────────────────────────────────────────────────────────────────
describe("ProjectHub（极简首页）", () => {
  /** 位置探针：显示 pathname+search，供断言跳转是否携带 ?turn=（轮 4 首问） */
  function LocationProbe() {
    const loc = useLocation();
    return <div data-testid="loc">{loc.pathname}{loc.search}</div>;
  }

  function renderHome() {
    return render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<ProjectHub />} />
          <Route path="/p/:id" element={<><div data-testid="workspace" /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>,
    );
  }

  test("中央输入 + 示例 chips + 密钥入口；旧版冗余区块不再出现", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([] as any);
    renderHome();
    expect(await screen.findByRole("heading", { name: "田野罗盘" })).toBeInTheDocument();
    expect(screen.getByText("面向社科研究者的田野调查方案制订与筹备工具")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/想研究什么/)).toBeInTheDocument();
    // 三个可点击示例 chips
    expect(screen.getByRole("button", { name: "村落宗族组织研究" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "非遗传承调查" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "乡村教育民族志" })).toBeInTheDocument();
    // 登录主钮（未登录）与发送按钮
    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建项目" })).toBeInTheDocument();
    // 旧版冗余信息不再出现：学科 chips / 导出格式 / 新建卡四阶段说明 / 侧栏学术纵深
    expect(screen.queryByText("社会学")).toBeNull();
    expect(screen.queryByText(/导出格式/)).toBeNull();
    expect(screen.queryByText(/四阶段/)).toBeNull();
    expect(screen.queryByText(/学术纵深/)).toBeNull();
    // 无项目时「继续先前项目」入口不渲染
    expect(screen.queryByRole("button", { name: /继续先前项目/ })).toBeNull();
  });

  test("点击示例 chip 填入完整示例文案", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([] as any);
    renderHome();
    fireEvent.click(await screen.findByRole("button", { name: "非遗传承调查" }));
    expect(screen.getByPlaceholderText(/想研究什么/)).toHaveValue(
      "调查某项非遗技艺的传承现状与传承人谱系，为期两个月",
    );
  });

  test("输入想法并创建：createProject({ purpose }) 后进入项目对话页", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([] as any);
    vi.mocked(rest.rest.createProject).mockResolvedValue({ project_id: "p7" } as any);
    renderHome();
    fireEvent.change(await screen.findByPlaceholderText(/想研究什么/), {
      target: { value: "研究茶马古道沿线村落的集市变迁" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    await waitFor(() =>
      expect(vi.mocked(rest.rest.createProject)).toHaveBeenCalledWith({
        purpose: "研究茶马古道沿线村落的集市变迁",
      }),
    );
    expect(await screen.findByTestId("workspace")).toBeInTheDocument();
  });

  test("Enter 直接创建，Shift+Enter 仅换行不创建", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([] as any);
    vi.mocked(rest.rest.createProject).mockResolvedValue({ project_id: "p8" } as any);
    renderHome();
    const input = await screen.findByPlaceholderText(/想研究什么/);
    fireEvent.change(input, { target: { value: "想法A" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(vi.mocked(rest.rest.createProject)).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    await waitFor(() => expect(vi.mocked(rest.rest.createProject)).toHaveBeenCalled());
  });

  test("登录弹层：默认隐藏，点「登录」打开，高级密钥区展开与 Escape 收起", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([] as any);
    renderHome();
    await screen.findByRole("heading", { name: "田野罗盘" });
    // 默认未挂载：密钥表单不在文档中（高级折叠区收起时不挂载 ApiKeySection）
    expect(screen.queryByPlaceholderText("sk-…")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("dialog", { name: "登录或注册" })).toBeInTheDocument();
    // 高级密钥区默认收起，点折叠条展开后 ApiKeySection 挂载
    expect(screen.queryByPlaceholderText("sk-…")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /高级 · API 密钥/ }));
    expect(screen.getByPlaceholderText("sk-…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // 再次打开后 Escape 收起
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    fireEvent.keyDown(await screen.findByRole("dialog", { name: "登录或注册" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  test("已有项目：折叠入口显示数量，展开后点项目进入对话页", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([
      { project_id: "p1", phase: "itinerary_planning", research_context: { discipline: "社会学", topic: "宗族变迁" }, counts: { sites: 2, informants: 4, proposals: 1 } },
    ] as any);
    renderHome();
    // 默认折叠：入口可见，项目本身不渲染
    const toggle = await screen.findByRole("button", { name: /继续先前项目（1）/ });
    expect(screen.queryByText(/宗族变迁/)).toBeNull();
    fireEvent.click(toggle);
    // 锚定开头：删除钮 aria-label 含“删除项目 宗族变迁”，会与纯正则多匹配
    fireEvent.click(await screen.findByRole("button", { name: /^宗族变迁/ }));
    expect(await screen.findByTestId("workspace")).toBeInTheDocument();
  });

  // ── 任务1b：项目卡显示链 title || topic || 未命名项目（projectId 永不作显示名） ──
  test("项目卡显示链：title 优先 / 回退 topic / 兑底未命名项目", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([
      { project_id: "p1", phase: "itinerary_planning", research_context: { topic: "宗族变迁", title: "宗族调研手记" }, counts: { sites: 0, informants: 0, proposals: 0 } },
      { project_id: "p2", phase: "research_positioning", research_context: { topic: "非遗传承" }, counts: { sites: 0, informants: 0, proposals: 0 } },
      { project_id: "p3", phase: "research_positioning", research_context: {}, counts: { sites: 0, informants: 0, proposals: 0 } },
    ] as any);
    renderHome();
    fireEvent.click(await screen.findByRole("button", { name: /继续先前项目（3）/ }));
    expect(await screen.findByText("宗族调研手记")).toBeInTheDocument(); // title 优先
    expect(screen.getByText("非遗传承")).toBeInTheDocument();           // 无 title 回退 topic
    expect(screen.getByText("未命名项目")).toBeInTheDocument();          // 皆空兑底
    // 字母+数字的 project_id 不作为显示名出现
    expect(screen.queryByText(/p[123]/)).toBeNull();
  });

  test("项目列表加载失败：如实提示且输入区可用", async () => {
    vi.mocked(rest.rest.listProjects).mockRejectedValue(new Error("network down"));
    renderHome();
    expect(await screen.findByText(/项目列表加载失败/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/想研究什么/)).toBeEnabled();
  });

  // 终审修项：列表 effect 依赖 [user]——登录/登出/会话失效后身份变化，
  // 按新 ownerHeaders（匿名令牌 ↔ 会话令牌）重拉，不再停留在旧身份的列表
  test("身份切换（user 变化）后重拉项目列表", async () => {
    vi.mocked(rest.rest.listProjects)
      .mockResolvedValueOnce([] as any)
      .mockResolvedValueOnce([
        { project_id: "p1", phase: "research_positioning", research_context: { topic: "宗族变迁" }, counts: { sites: 0, informants: 0, proposals: 0 } },
      ] as any);
    renderHome();
    await screen.findByRole("heading", { name: "田野罗盘" });
    expect(vi.mocked(rest.rest.listProjects)).toHaveBeenCalledTimes(1);
    // 模拟登录成功：auth store 的 user 从 null → 对象
    act(() => {
      useAuthStore.setState({ user: { user_id: "u1", email: "a@b.com", created_at: "2026-10-20T00:00:00" } });
    });
    await waitFor(() => expect(vi.mocked(rest.rest.listProjects)).toHaveBeenCalledTimes(2));
    // 新身份的列表上屏：折叠入口出现
    expect(await screen.findByRole("button", { name: /继续先前项目（1）/ })).toBeInTheDocument();
  });

  // A02：切换瞬间清空旧身份数据（修复前旧列表在请求返回前仍可见）
  test("身份切换瞬间清空旧项目列表，等待新身份数据", async () => {
    vi.mocked(rest.rest.listProjects)
      .mockResolvedValueOnce([
        { project_id: "p1", phase: "research_positioning", research_context: { topic: "宗族变迁" }, counts: { sites: 0, informants: 0, proposals: 0 } },
      ] as any)
      .mockReturnValueOnce(new Promise(() => {}) as any);   // 新身份请求悬置
    renderHome();
    expect(await screen.findByRole("button", { name: /继续先前项目（1）/ })).toBeInTheDocument();
    act(() => {
      useAuthStore.setState({ user: { user_id: "u1", email: "a@b.com", created_at: "2026-10-20T00:00:00" } });
    });
    // 切换瞬间旧列表入口消失（修复前仍显示「继续先前项目（1）」直到第二次返回）
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /继续先前项目/ })).toBeNull());
  });

  // A02：退出且网络失败 → 不残留旧数据，如实提示
  test("退出后列表重拉失败：旧列表清空且提示错误", async () => {
    act(() => {
      useAuthStore.setState({ user: { user_id: "u0", email: "old@b.com", created_at: "2026-10-20T00:00:00" } });
    });
    vi.mocked(rest.rest.listProjects)
      .mockResolvedValueOnce([
        { project_id: "p1", phase: "research_positioning", research_context: { topic: "宗族变迁" }, counts: { sites: 0, informants: 0, proposals: 0 } },
      ] as any)
      .mockRejectedValueOnce(new Error("network down"));
    renderHome();
    expect(await screen.findByRole("button", { name: /继续先前项目（1）/ })).toBeInTheDocument();
    act(() => { useAuthStore.setState({ user: null }); });   // 退出
    expect(await screen.findByText(/项目列表加载失败/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /继续先前项目/ })).toBeNull();
  });

  // ── 轮 4：首页一句话即对话起点 ──
  // 此前 purpose 只写 research_context 不发 turn，跳转后对话页「尚无对话」，输入被
  // 静默吞进右栏；现在创建项目后把 purpose 原文发为首轮 turn，跳转携带 ?turn= 供
  // 对话页订阅回放（adopt 行为见 useTurn.spec / workspace.spec）。
  test("创建即开聊：purpose 同时发为首轮 turn，跳转携带 ?turn=", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([] as any);
    vi.mocked(rest.rest.createProject).mockResolvedValue({ project_id: "p7" } as any);
    vi.mocked(rest.rest.createTurn).mockResolvedValue({ turn_id: "t7", status: "running" } as any);
    renderHome();
    fireEvent.change(await screen.findByPlaceholderText(/想研究什么/), {
      target: { value: "研究宗族组织" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    await waitFor(() =>
      expect(vi.mocked(rest.rest.createTurn)).toHaveBeenCalledWith("p7", "研究宗族组织"));
    expect(await screen.findByTestId("loc")).toHaveTextContent("/p/p7?turn=t7");
  });

  test("首轮 turn 建立失败（如未配 key 403）：不阻塞进入项目页", async () => {
    vi.mocked(rest.rest.listProjects).mockResolvedValue([] as any);
    vi.mocked(rest.rest.createProject).mockResolvedValue({ project_id: "p8" } as any);
    vi.mocked(rest.rest.createTurn).mockRejectedValue(new Error("403 llm_key_required"));
    renderHome();
    fireEvent.change(await screen.findByPlaceholderText(/想研究什么/), {
      target: { value: "想法B" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    // 项目本身已建好：仍进入项目页，只是不带 ?turn=（对话页降级文案/手动首问兜底）
    expect(await screen.findByTestId("workspace")).toBeInTheDocument();
    expect(screen.getByTestId("loc")).toHaveTextContent("/p/p8");
    expect(screen.getByTestId("loc")).not.toHaveTextContent("turn=");
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

// ──────────────────────────────────────────────────────────────────────────
// 终审#1：提案下载必须走 fetch + authHeaders()（/projects 前缀受 F02 令牌保护，
// 顶层锚点导航带不了自定义头，启用 App Token 时必 401）。
// ──────────────────────────────────────────────────────────────────────────
describe("ProposalView 下载（终审#1）", () => {
  const doc = {
    id: "doc1", kind: "proposal", version: "draft",
    markdown: "# 一、选题依据\n\n正文", sections: ["一、选题依据"],
    requested_mode: "", mode: "", inputs_hash: "",
  };
  const createObjectURL = vi.fn(() => "blob:proposal-md");
  const revokeObjectURL = vi.fn();
  let calls: Array<[string, RequestInit | undefined]> = [];

  beforeEach(() => {
    localStorage.clear();
    useExportFormatStore.setState({ format: "md" });
    saveKeys("", "", "tok-9"); // authHeaders() 必须在点击时读取：新存令牌即时生效
    calls = [];
    // jsdom 未实现 createObjectURL/revokeObjectURL，注入可断言替身
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true, writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true, writable: true });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return { ok: true, blob: async () => new Blob(["# 研究方案"]) } as unknown as Response;
    }));
    vi.mocked(rest.rest.getProposal).mockResolvedValue(doc as any);
    vi.mocked(rest.rest.listProposals).mockResolvedValue([{ id: "doc1", kind: "proposal", version: "draft" }]);
    vi.mocked(rest.rest.getProject).mockResolvedValue({ research_context: { topic: "宗族变迁" }, phase: "research_positioning" } as any);
    vi.mocked(rest.rest.proposalDownloadUrl).mockImplementation((id: string) => `/projects/${id}/proposal/download`);
    vi.mocked(rest.rest.proposalByIdDownloadUrl).mockImplementation(
      (id: string, docId: string, format?: string) =>
        format && format !== "md"
          ? `/projects/${id}/proposals/${docId}/download?format=${format}`
          : `/projects/${id}/proposals/${docId}/download`,
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  function renderView() {
    return render(
      <MemoryRouter initialEntries={["/p/p9/proposal"]}>
        <Routes>
          <Route path="/p/:id/proposal" element={<ProposalView />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  test("下载走 fetch 并注入 X-App-Token，blob → objectURL 临时链", async () => {
    renderView();
    const btn = await screen.findByRole("button", { name: /下载 \.md/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(calls[0][0]).toBe("/projects/p9/proposals/doc1/download");
    expect(calls[0][1]?.headers).toMatchObject({ "X-App-Token": "tok-9" });
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:proposal-md");
  });

  test("失败（如 401）显示下载失败提示，不触发 objectURL", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 401 } as unknown as Response)));
    renderView();
    const btn = await screen.findByRole("button", { name: /下载 \.md/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByText(/下载失败/)).toBeInTheDocument());
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  test("偏好 docx 时：按钮文案 .docx，by-id fetch 追加 ?format=docx", async () => {
    useExportFormatStore.setState({ format: "docx" });
    renderView();
    const btn = await screen.findByRole("button", { name: /下载 \.docx/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(calls[0][0]).toBe("/projects/p9/proposals/doc1/download?format=docx");
    expect(calls[0][1]?.headers).toMatchObject({ "X-App-Token": "tok-9" });
    useExportFormatStore.setState({ format: "md" });
  });
});
