import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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
 *    且加载快照后 **activeTab 仍是 progress**（红线①：不自动切 Tab）；
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

/** 桩 fetch：按 URL 分发最小 Response（/settings/status → 已配置；其余 → rest payload）。 */
function stubFetch(opts: { rest: unknown; restOk?: boolean; settings?: unknown }) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/settings/status") {
      const p = opts.settings ?? { dashscope_configured: true, flyai_configured: true };
      return { ok: true, status: 200, json: async () => p, text: async () => String(p) };
    }
    const ok = opts.restOk ?? true;
    return { ok, status: ok ? 200 : 500, json: async () => opts.rest, text: async () => String(opts.rest) };
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
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    expect(screen.getByText(/尚无对话/)).toBeInTheDocument();
    // 右栏：默认进度面板；红线①——快照/阶段到位也不自动切 Tab
    expect(screen.getByText(/采集进度/)).toBeInTheDocument();
    expect(useUiStore.getState().activeTab).toBe("progress");

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
});

describe("Workspace 无 key 拦截（可浏览不可发）", () => {
  test("本地无 key 且服务端未配：Composer 禁用 + 提示回首页配置，快照仍可浏览", async () => {
    stubFetch({ rest: SNAP, settings: { dashscope_configured: false, flyai_configured: false } });
    render(<App />);

    // 可浏览：快照照常加载
    expect(await screen.findByText("白族村落宗族组织变迁")).toBeInTheDocument();
    // 不可发：输入框禁用 + 锁提示
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByTestId("composer-lock-hint")).toHaveTextContent("未配置百炼 API Key");
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  });

  test("本地已存 key：不锁且不发 /settings/status（免查直通）", async () => {
    saveKeys("sk-local", "");
    const fn = stubFetch({ rest: SNAP });
    render(<App />);

    expect(await screen.findByText("白族村落宗族组织变迁")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).not.toBeDisabled();
    expect(fn.mock.calls.map((c) => c[0])).not.toContain("/settings/status");
  });

  test("服务端已配（环境变量部署）：本地无 key 也不锁", async () => {
    stubFetch({ rest: SNAP }); // settings 默认 configured:true
    render(<App />);

    expect(await screen.findByText("白族村落宗族组织变迁")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).not.toBeDisabled();
    expect(screen.queryByTestId("composer-lock-hint")).not.toBeInTheDocument();
  });
});
