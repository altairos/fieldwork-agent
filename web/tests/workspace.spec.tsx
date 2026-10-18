import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "../src/App";
import { saveKeys } from "../src/lib/keys";
import { useConversationStore } from "../src/stores/conversation";
import { useProjectStore } from "../src/stores/project";
import { useToolsStore } from "../src/stores/tools";
import { useUiStore } from "../src/stores/ui";
import type { ProjectSnapshot } from "../src/types/models";

/**
 * Workspace 接线集成测试（App.tsx，Task 7 的接线部分）。
 *
 * 覆盖三件在组件级测试里看不到的事：
 * 1. 路由 `/p/:id` → `useParams` 取 id → 挂载即 `GET /projects/{id}` → `project.loadSnapshot`
 *    （右栏五面板的 REST 侧数据源；红线②：整个工作台只有这一次快照请求，面板不再各自取数）；
 * 2. 中栏三件（PhaseBanner / Timeline / Composer）与右栏 RightDock 同时在位，
 *    且加载快照后 **activeTab 仍是 basic-info**（红线①：不自动切 Tab）；
 * 3. 快照请求失败 → 经 ui store 的 error 事件如实上屏（PhaseBanner 降级横幅），不静默吞错。
 *
 * 用 `history.pushState` 驱动 BrowserRouter（不导出 Workspace，避免为测试放宽 App 的公开面）。
 */
const SNAP: ProjectSnapshot = {
  project_id: "p1",
  phase: "itinerary_planning",
  research_context: { discipline: "社会学", topic: "白族村落宗族组织变迁" },
  field_sites: [{ name: "喜洲镇", confirmed: true, profile: null }],
  informants: [],
  proposals: [],
  phase_history: [],
};

/** 桩 fetch：按 URL 分发最小 Response（/settings/status → 已配置；/messages → 对话史；
 *  /turns/:id/events → SSE 回放（轮 4 adopt）；其余 → rest payload）。 */
function stubFetch(opts: {
  rest: unknown; restOk?: boolean; restStatus?: number; restBody?: string;
  settings?: unknown; messages?: unknown; messagesStatus?: number; messagesBody?: string;
  sse?: Record<string, Uint8Array[]>;
}) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/settings/status") {
      const p = opts.settings ?? { dashscope_configured: true, flyai_configured: true };
      return { ok: true, status: 200, json: async () => p, text: async () => String(p) };
    }
    if (url === "/projects/p1/messages") {
      const st = opts.messagesStatus ?? 200;
      const p = opts.messages ?? [];
      return { ok: st < 400, status: st, json: async () => p, text: async () => (opts.messagesBody ?? String(p)) };
    }
    const sseMatch = url.match(/^\/projects\/p1\/turns\/([^/]+)\/events$/);
    if (sseMatch) {
      const queue = [...(opts.sse?.[sseMatch[1]] ?? [])];
      return { ok: true, status: 200, body: { getReader: () => ({
        read: async () => (queue.length
          ? { value: queue.shift() as Uint8Array, done: false }
          : { value: undefined, done: true }),
        cancel: async () => {},
      }) } };
    }
    const ok = opts.restOk ?? true;
    const st = opts.restStatus ?? (ok ? 200 : 500);
    return { ok: st < 400, status: st, json: async () => opts.rest, text: async () => (opts.restBody ?? String(opts.rest)) };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => {
  localStorage.clear();
  useConversationStore.getState().reset();
  useToolsStore.getState().reset();
  useProjectStore.getState().reset();
  useUiStore.getState().reset();
  window.history.pushState({}, "", "/p/p1");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Workspace 接线（App.tsx）", () => {
  test("挂载即拉取项目快照，中栏对话流与右栏面板就位", async () => {
    const fn = stubFetch({ rest: SNAP });
    render(<App />);

    // 中栏：Composer 输入框 + 时间线空态（尚无回合）
    expect(screen.getByRole("textbox", { name: "消息输入" })).toBeInTheDocument();
    expect(screen.getByText(/尚无对话/)).toBeInTheDocument();
    // 右栏：默认基本信息面板；红线①——快照/阶段到位也不自动切 Tab
    expect(screen.getByText(/基本信息（可随时编辑）/)).toBeInTheDocument();
    expect(useUiStore.getState().activeTab).toBe("basic-info");

    // 快照加载完成后：Header 项目名取 research_context.topic
    expect(await screen.findByText("白族村落宗族组织变迁")).toBeInTheDocument();
    expect(fn.mock.calls.map((c) => c[0])).toContain("/projects/p1");
    expect(useProjectStore.getState().snapshot?.field_sites).toHaveLength(1);
    expect(useProjectStore.getState().phase).toBe("itinerary_planning");
  });

  test("快照加载失败经 ui store 的 error 事件上屏（不静默吞错）", async () => {
    stubFetch({ rest: "boom", restOk: false, settings: { dashscope_configured: true, flyai_configured: true } });
    render(<App />);
    expect(await screen.findByText(/项目快照加载失败/)).toBeInTheDocument();
    expect(screen.getByText(/snapshot_failed/)).toBeInTheDocument();
    expect(useProjectStore.getState().snapshot).toBeNull();
  });

  test("挂载即回填对话史（刷新恢复）：历史消息上屏，空态消失", async () => {
    const fn = stubFetch({ rest: SNAP, messages: [
      { role: "user", content: "帮我定位选题" },
      { role: "assistant", content: "已收到，先聊研究动机" },
    ] });
    render(<App />);

    // 历史消息经 hydrate 上屏；「尚无对话」空态不再出现（刷新丢史回归钉）
    expect(await screen.findByText("帮我定位选题")).toBeInTheDocument();
    expect(screen.getByText("已收到，先聊研究动机")).toBeInTheDocument();
    expect(screen.queryByText(/尚无对话/)).not.toBeInTheDocument();
    expect(fn.mock.calls.map((c) => c[0])).toContain("/projects/p1/messages");
  });
});

describe("Workspace 无 key 拦截（可浏览不可发）", () => {
  test("本地无 key 且服务端未配：Composer 禁用 + 提示回首页配置，快照仍可浏览", async () => {
    stubFetch({ rest: SNAP, settings: { dashscope_configured: false, flyai_configured: false } });
    render(<App />);

    // 可浏览：快照照常加载
    expect(await screen.findByText("白族村落宗族组织变迁")).toBeInTheDocument();
    // 不可发：输入框禁用 + 锁提示
    expect(screen.getByRole("textbox", { name: "消息输入" })).toBeDisabled();
    expect(screen.getByTestId("composer-lock-hint")).toHaveTextContent("未配置百炼 API Key");
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  });

  test("本地已存 key：不锁且不发 /settings/status（免查直通）", async () => {
    saveKeys("sk-local", "");
    const fn = stubFetch({ rest: SNAP });
    render(<App />);

    expect(await screen.findByText("白族村落宗族组织变迁")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "消息输入" })).not.toBeDisabled();
    expect(fn.mock.calls.map((c) => c[0])).not.toContain("/settings/status");
  });

  test("服务端已配（环境变量部署）：本地无 key 也不锁", async () => {
    stubFetch({ rest: SNAP }); // settings 默认 configured:true
    render(<App />);

    expect(await screen.findByText("白族村落宗族组织变迁")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "消息输入" })).not.toBeDisabled();
    expect(screen.queryByTestId("composer-lock-hint")).not.toBeInTheDocument();
  });

  // P5：锁定后补填 key 的两条解锁路径（原实现只在挂载时查一次，
  // 回首页保存后退回（bfcache）或就地补填都不会重查 → 误显“未配置”）
  test("P5：锁定提示就地弹密钥层，保存百炼 Key 后关闭即解锁（不离开本页）", async () => {
    stubFetch({ rest: SNAP, settings: { dashscope_configured: false, flyai_configured: false } });
    render(<App />);
    expect(await screen.findByTestId("composer-lock-hint")).toHaveTextContent("未配置百炼 API Key");

    // 提示内按钮就地弹层（免回首页）
    fireEvent.click(screen.getByRole("button", { name: "填写百炼 API Key" }));
    expect(await screen.findByRole("dialog", { name: "密钥与登录" })).toBeInTheDocument();

    // 弹层里填 key 保存，关闭 → 解锁
    fireEvent.change(screen.getByLabelText(/百炼 API Key/), { target: { value: "sk-late" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "消息输入" })).not.toBeDisabled());
    expect(screen.queryByTestId("composer-lock-hint")).not.toBeInTheDocument();
  });

  test("P5：bfcache 恢复（pageshow persisted）重查本地 key：他处保存的 key 即刻解锁", async () => {
    stubFetch({ rest: SNAP, settings: { dashscope_configured: false, flyai_configured: false } });
    render(<App />);
    expect(await screen.findByTestId("composer-lock-hint")).toBeInTheDocument();

    // 用户在首页（或另一标签）保存了 key，本页从 bfcache 恢复
    saveKeys("sk-elsewhere", "");
    const ev = new Event("pageshow");
    Object.defineProperty(ev, "persisted", { value: true });
    window.dispatchEvent(ev);

    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "消息输入" })).not.toBeDisabled());
  });
});

describe("Workspace 接住首页首轮回合（?turn= 领养，轮 4）", () => {
  test("?turn= 挂载：订阅该回合回放、正文上屏、replace 清 query、史一致", async () => {
    const encoder = new TextEncoder();
    const frames = (rows: string[]) => rows.map((r) => encoder.encode(r));
    const fn = stubFetch({
      rest: SNAP,
      messages: [
        { role: "user", content: "研究宗族组织" },
        { role: "assistant", content: "你好，田野罗盘" },
      ],
      sse: {
        t9: frames([
          'id: 1\nevent: message_delta\ndata: {"turn_id":"t9","seq":1,"text":"你好，"}\n\n',
          'id: 2\nevent: message_delta\ndata: {"turn_id":"t9","seq":2,"text":"田野罗盘"}\n\n',
          'id: 3\nevent: turn_done\ndata: {"turn_id":"t9","seq":3,"reply_id":"t9","usage":{}}\n\n',
        ]),
      },
    });
    window.history.pushState({}, "", "/p/p1?turn=t9");
    render(<App />);

    // 回放正文上屏，空态消失——「跳转后尚无对话」的直接回归钉
    expect(await screen.findByText("你好，田野罗盘")).toBeInTheDocument();
    expect(screen.queryByText(/尚无对话/)).not.toBeInTheDocument();
    // 订阅了首页创建的首轮回合
    expect(fn.mock.calls.map((c) => c[0])).toContain("/projects/p1/turns/t9/events");
    // 接住后立即 replace 清 query（防刷新重放/分享链接重触发）
    await waitFor(() => expect(window.location.search).toBe(""));
    // 最终与后端事实一致：[user 首问, assistant 回复]
    expect(useConversationStore.getState().messages).toEqual([
      { role: "user", content: "研究宗族组织" },
      { role: "assistant", content: "你好，田野罗盘" },
    ]);
  });
});

describe("Workspace 访问令牌 401（app_token_required）降级", () => {
  /** 后端 App Token 守卫的 401 响应体（web_host/app.py 审计 F02）原样 */
  const AUTH_401 = JSON.stringify({
    code: "app_token_required",
    message: "缺少或错误的 X-App-Token 请求头：请在首页「API 密钥」卡填写访问令牌",
  });

  /** 守卫式桩：模拟服务端 App Token 中间件——X-App-Token ≠ tok-good 一律 401；
 *  令牌正确则按 URL 回正常数据（保存新令牌后可就地恢复，无需整页刷新）。 */
  function stubAuthGuard(opts: { rest: unknown; messages?: unknown }) {
    const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      if (headers["X-App-Token"] !== "tok-good") {
        return { ok: false, status: 401, json: async () => ({}), text: async () => AUTH_401 };
      }
      const url = String(input);
      if (url === "/settings/status") {
        const p = { dashscope_configured: true, flyai_configured: true };
        return { ok: true, status: 200, json: async () => p, text: async () => String(p) };
      }
      if (url === "/projects/p1/messages") {
        const p = opts.messages ?? [];
        return { ok: true, status: 200, json: async () => p, text: async () => String(p) };
      }
      return { ok: true, status: 200, json: async () => opts.rest, text: async () => String(opts.rest) };
    });
    vi.stubGlobal("fetch", fn);
    return fn;
  }

  test("快照/消息史 401：横幅给人话指引（不透传原始 JSON）并自动弹出密钥弹层", async () => {
    stubAuthGuard({ rest: SNAP });
    render(<App />);

    // 弹层自动打开——用户不必回首页找「密钥 / 登录」入口
    expect(await screen.findByRole("dialog", { name: "密钥与登录" })).toBeInTheDocument();
    // 横幅是人话指引；原始 JSON（app_token_required 等响应体文本）不上屏
    expect(await screen.findByText(/访问令牌缺失或已失效/)).toBeInTheDocument();
    expect(screen.queryByText(/app_token_required/)).not.toBeInTheDocument();
  });

  test("弹层中保存新令牌后关闭：就地重试恢复快照与消息史，横幅撤下", async () => {
    const fn = stubAuthGuard({ rest: SNAP, messages: [{ role: "user" as const, content: "帮我定位选题" }] });
    render(<App />);
    const dialog = await screen.findByRole("dialog", { name: "密钥与登录" });

    fireEvent.change(within(dialog).getByLabelText(/访问令牌/), { target: { value: "tok-good" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));

    // 新令牌带上重试：快照与消息史恢复，401 横幅撤下，弹层已关
    expect(await screen.findByText("白族村落宗族组织变迁")).toBeInTheDocument();
    expect(await screen.findByText("帮我定位选题")).toBeInTheDocument();
    expect(screen.queryByText(/访问令牌缺失或已失效/)).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "密钥与登录" })).not.toBeInTheDocument();
    expect(fn.mock.calls.filter((c) => String(c[0]) === "/projects/p1").length).toBe(2);
  });

  test("未改令牌直接关闭弹层：不重试，横幅如实保留", async () => {
    const fn = stubAuthGuard({ rest: SNAP });
    render(<App />);
    const dialog = await screen.findByRole("dialog", { name: "密钥与登录" });

    fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "密钥与登录" })).not.toBeInTheDocument());
    expect(screen.getByText(/访问令牌缺失或已失效/)).toBeInTheDocument();
    expect(fn.mock.calls.filter((c) => String(c[0]) === "/projects/p1").length).toBe(1);
  });
});

describe("Workspace 降级横幅撤除精确性（兄弟请求互不误撤）", () => {
  /** 受控时序：失败侧横幅先上屏、成功侧后落地——排除并行请求微任务排序的
 *  偶然性，确定性验证「成功回调只撤同 code 的横幅，不误撤兄弟请求的失败提示」。 */
  test("快照 500 而消息史成功：snapshot_failed 横幅不被消息史成功侧误撤", async () => {
    let releaseMessages!: (msgs: unknown) => void;
    const gate = new Promise<unknown>((res) => { releaseMessages = res; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/settings/status") {
        const p = { dashscope_configured: true, flyai_configured: true };
        return { ok: true, status: 200, json: async () => p, text: async () => String(p) };
      }
      if (url === "/projects/p1/messages") {
        return { ok: true, status: 200, json: async () => gate, text: async () => "" };
      }
      return { ok: false, status: 500, json: async () => ({}), text: async () => "internal error" };
    }));
    render(<App />);

    // 失败侧横幅先上屏
    expect(await screen.findByText(/项目快照加载失败/)).toBeInTheDocument();

    // 消息史成功落地（hydrate + 成功回调执行完毕）
    releaseMessages([{ role: "user", content: "帮我定位选题" }]);
    expect(await screen.findByText("帮我定位选题")).toBeInTheDocument();

    // 横幅仍在——错误指示不得被兄弟请求的成功静默吞掉
    expect(screen.getByText(/项目快照加载失败/)).toBeInTheDocument();
  });

  test("消息史 500 而快照成功：history_failed 横幅不被快照成功侧误撤", async () => {
    let releaseRest!: (snap: unknown) => void;
    const gate = new Promise<unknown>((res) => { releaseRest = res; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/settings/status") {
        const p = { dashscope_configured: true, flyai_configured: true };
        return { ok: true, status: 200, json: async () => p, text: async () => String(p) };
      }
      if (url === "/projects/p1/messages") {
        return { ok: false, status: 500, json: async () => ({}), text: async () => "db down" };
      }
      return { ok: true, status: 200, json: async () => gate, text: async () => "" };
    }));
    render(<App />);

    expect(await screen.findByText(/对话历史加载失败/)).toBeInTheDocument();

    releaseRest(SNAP);
    expect(await screen.findByText("白族村落宗族组织变迁")).toBeInTheDocument();

    expect(screen.getByText(/对话历史加载失败/)).toBeInTheDocument();
  });
});
