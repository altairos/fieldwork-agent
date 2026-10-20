import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render } from "@testing-library/react";
import { disposeGuestProject, useDisposeGuestProject } from "../src/hooks/useDisposeGuestProject";

/**
 * 匿名会话离开即清除记录（2026-10-20 用户裁决）：
 * 关闭页面 / 返回首页删除项目；F5、Ctrl+R、bfcache 豁免；注册后不删。
 * jsdom 无页面生命周期，直接派发 pagehide/beforeunload 事件断言行为。
 */

function Guest({ projectId, enabled }: { projectId: string; enabled: boolean }) {
  useDisposeGuestProject(projectId, enabled);
  return null;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => ({ ok: true, status: 204, text: async () => "" }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});

/** 派发 pagehide（可指定 bfcache 的 persisted）。 */
function firePageHide(persisted = false) {
  const ev = new Event("pagehide");
  Object.defineProperty(ev, "persisted", { value: persisted });
  window.dispatchEvent(ev);
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

  test("disposeGuestProject 可直接调用（返回首页等 SPA 离开路径）", () => {
    disposeGuestProject("p2");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/projects/p2");
    expect(init.method).toBe("DELETE");
  });

  test("浏览器后退离开工作台才删；项目内页间跳转不删", () => {
    render(<Guest projectId="p1" enabled />);
    window.history.pushState({}, "", "/p/p1/proposal");   // 方案页：同一项目
    window.dispatchEvent(new Event("popstate"));
    expect(fetchMock).not.toHaveBeenCalled();

    window.history.pushState({}, "", "/p/p10");           // 前缀相同的别的项目
    window.dispatchEvent(new Event("popstate"));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    window.history.pushState({}, "", "/");                // 退回首页
    window.dispatchEvent(new Event("popstate"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
