import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { disposeGuestProject, useDisposeGuestProject } from "../src/hooks/useDisposeGuestProject";
import { setSessionToken } from "../src/lib/session";

/**
 * 匿名会话离开即清除记录（2026-10-20 用户裁决）：
 * 关闭页面 / 返回首页 / 浏览器后退删除项目；F5、Ctrl+R、bfcache 豁免；注册后不删。
 *
 * T04：后退路径必须用真实 BrowserRouter + history.back() 覆盖。旧单测只改 history
 * 再手动派发 popstate，没有真实路由切换/组件卸载，掩盖了“Router 先处理 popstate
 * 并卸载工作区、页面级监听来不及触发”的漏删。
 */

function Guest({ projectId, enabled }: { projectId: string; enabled: boolean }) {
  useDisposeGuestProject(projectId, enabled);
  return null;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  try { localStorage.clear(); } catch { /* jsdom 无 localStorage 时忽略 */ }
  window.history.replaceState({}, "", "/");
  fetchMock = vi.fn(async () => ({ ok: true, status: 204, text: async () => "" }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  // 先卸载再复位：避免 teardown 时“地址已被改成 /”被误读为离开项目，
  // 又把真实 fetch（相对 URL）发到 jsdom 之外
  cleanup();
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

/** 派发 pagehide（可指定 bfcache 的 persisted）。 */
function firePageHide(persisted = false) {
  const ev = new Event("pagehide");
  Object.defineProperty(ev, "persisted", { value: persisted });
  window.dispatchEvent(ev);
}

// ---- 真实浏览器路由（T04 验收：后退/前进、返回首页、站内切视图）----

function WorkspaceStub() {
  const { id } = useParams();
  const navigate = useNavigate();
  useDisposeGuestProject(id ?? "", true);
  return (
    <div>
      <div>workspace:{id}</div>
      <button onClick={() => navigate(`/p/${id}/proposal`)}>查看方案</button>
      <button onClick={() => navigate("/")}>返回首页</button>
    </div>
  );
}

function HomeStub() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/p/p1")}>进入项目</button>;
}

function RouterApp({ basename }: { basename?: string }) {
  return (
    <BrowserRouter basename={basename}>
      <Routes>
        <Route path="/" element={<HomeStub />} />
        <Route path="/p/:id" element={<WorkspaceStub />} />
        <Route path="/p/:id/proposal" element={<WorkspaceStub />} />
      </Routes>
    </BrowserRouter>
  );
}

async function enterProject() {
  fireEvent.click(screen.getByRole("button", { name: "进入项目" }));
  expect(await screen.findByText("workspace:p1")).toBeTruthy();
}

async function goBack() {
  await act(async () => { window.history.back(); });
  await waitFor(() => expect(screen.queryByText("workspace:p1")).toBeNull());
}

describe("匿名会话离开清除项目", () => {
  test("pagehide → DELETE /projects/{id}（keepalive，带归属头）", () => {
    render(<Guest projectId="p1" enabled />);
    firePageHide();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/projects/p1");
    expect(init.method).toBe("DELETE");
    expect(init.keepalive).toBe(true);
    expect((init.headers as Record<string, string>)["X-Owner-Token"]).toBeTruthy();
  });

  test("已登录（enabled=false）不挂监听：只浏览不删除", () => {
    render(<Guest projectId="p1" enabled={false} />);
    firePageHide();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("F5 / Ctrl+R 刷新豁免：不删除", () => {
    const { unmount } = render(<Guest projectId="p1" enabled />);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "F5" }));
    firePageHide();
    expect(fetchMock).not.toHaveBeenCalled();
    unmount();

    render(<Guest projectId="p1" enabled />);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "r", ctrlKey: true }));
    firePageHide();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("bfcache（persisted=true）不删除：用户可能按返回键回来", () => {
    render(<Guest projectId="p1" enabled />);
    firePageHide(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("beforeunload 触发浏览器原生确认（defaultPrevented）", () => {
    render(<Guest projectId="p1" enabled />);
    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
  });

  test("disposeGuestProject 可直接调用（未走路由的离开路径）", () => {
    disposeGuestProject("p2");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/projects/p2");
    expect(init.method).toBe("DELETE");
  });

  test("T04 浏览器后退离开工作台 → 发 DELETE（真实 BrowserRouter）", async () => {
    render(<RouterApp />);
    await enterProject();
    await goBack();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/projects/p1");
    expect(init.method).toBe("DELETE");
  });

  test("T04 子路径部署（basename）后退同样清理", async () => {
    vi.stubEnv("BASE_URL", "/fieldpass/");
    window.history.replaceState({}, "", "/fieldpass/");
    render(<RouterApp basename="/fieldpass" />);
    await enterProject();
    expect(window.location.pathname).toBe("/fieldpass/p/p1");
    await goBack();
    expect(window.location.pathname).toBe("/fieldpass/");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/projects/p1");
  });

  test("T04 站内切视图（工作台 → 方案页）不删除", async () => {
    render(<RouterApp />);
    await enterProject();
    fireEvent.click(screen.getByRole("button", { name: "查看方案" }));
    await waitFor(() => expect(window.location.pathname).toBe("/p/p1/proposal"));
    await act(async () => { await Promise.resolve(); });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("T04 返回首页按钮（SPA 导航）→ 删除且只发一次", async () => {
    render(<RouterApp />);
    await enterProject();
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    await waitFor(() => expect(screen.queryByText("workspace:p1")).toBeNull());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/projects/p1");
  });

  test("T04 注册认领后离开不删（会话令牌存在即豁免）", async () => {
    render(<RouterApp />);
    await enterProject();
    setSessionToken("sess_claimed");
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    await waitFor(() => expect(screen.queryByText("workspace:p1")).toBeNull());
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
