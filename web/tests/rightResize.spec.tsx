// web/tests/rightResize.spec.tsx —— 右栏边缘拖拽调宽（P3）
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "../src/App";
import { saveKeys } from "../src/lib/keys";
import { useConversationStore } from "../src/stores/conversation";
import { useProjectStore } from "../src/stores/project";
import { useToolsStore } from "../src/stores/tools";
import { useUiStore } from "../src/stores/ui";
import type { ProjectSnapshot } from "../src/types/models";

/**
 * P3 拖拽调宽的接线测试（App.tsx）：宽度经 `--right-w` CSS 变量驱动布局 grid
 * 第三列，把手是右栏左边缘的 separator。覆盖四件事：
 * 1. 把手在位（separator role，键盘/读屏可达）；
 * 2. 拖拽（pointerdown → window pointermove）实时改宽；松手后不再跟随；
 * 3. clamp 到 [280, 560]，拖出界不越限；
 * 4. localStorage 持久化，重挂载恢复上次宽度。
 *
 * 桩 fetch / 渲染先例同 workspace.spec.tsx（history.pushState 驱动路由）。
 */
const SNAP: ProjectSnapshot = {
  project_id: "p1",
  phase: "itinerary_planning",
  research_context: { topic: "白族村落宗族组织变迁" },
  field_sites: [],
  informants: [],
  proposals: [],
  phase_history: [],
};

function stubFetch() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (url === "/settings/status") {
      const p = { dashscope_configured: true, flyai_configured: true };
      return { ok: true, status: 200, json: async () => p, text: async () => String(p) } as unknown as Response;
    }
    if (url === "/projects/p1/messages") {
      return { ok: true, status: 200, json: async () => [], text: async () => "[]" } as unknown as Response;
    }
    return { ok: true, status: 200, json: async () => SNAP, text: async () => String(SNAP) } as unknown as Response;
  }));
}

/** 取承载 --right-w 的布局容器，读当前 CSS 变量值（如 "440px"）。 */
function rightW(container: HTMLElement): string {
  const el = container.querySelector<HTMLElement>('[style*="--right-w"]');
  expect(el).not.toBeNull();
  return el!.style.getPropertyValue("--right-w");
}

beforeEach(() => {
  localStorage.clear();
  saveKeys("", "", "");
  useConversationStore.getState().reset();
  useToolsStore.getState().reset();
  useProjectStore.getState().reset();
  useUiStore.getState().reset();
  window.history.pushState({}, "", "/p/p1");
  stubFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("右栏拖拽调宽（P3）", () => {
  test("把手在位；向左拖 100px → --right-w 340px→440px；松手后不再跟随", async () => {
    const { container } = render(<App />);
    const handle = await screen.findByRole("separator", { name: "调整右栏宽度" });
    expect(rightW(container)).toBe("340px");

    fireEvent.pointerDown(handle, { clientX: 1000 });
    fireEvent.pointerMove(window, { clientX: 900 }); // 向左 100px → 变宽
    await waitFor(() => expect(rightW(container)).toBe("440px"));

    fireEvent.pointerUp(window);
    fireEvent.pointerMove(window, { clientX: 800 }); // 松手后拖拽已结束
    await waitFor(() => expect(rightW(container)).toBe("440px"));
  });

  test("拖出界 clamp 到 [280, 560]", async () => {
    const { container } = render(<App />);
    const handle = await screen.findByRole("separator", { name: "调整右栏宽度" });

    fireEvent.pointerDown(handle, { clientX: 1000 });
    fireEvent.pointerMove(window, { clientX: -100000 }); // 大幅向左 → 上限
    await waitFor(() => expect(rightW(container)).toBe("560px"));

    fireEvent.pointerUp(window);
    fireEvent.pointerDown(handle, { clientX: 1000 });
    fireEvent.pointerMove(window, { clientX: 100000 }); // 大幅向右 → 下限
    await waitFor(() => expect(rightW(container)).toBe("280px"));
  });

  test("pointerdown 即 preventDefault：拖拽途中不触发浏览器原生文本选择", async () => {
    // 把手贴右栏左边缘，拖拽必经中栏正文：不 preventDefault 时按住鼠标横拖
    // 会选中文本（蓝底反白，评审 M2）。fireEvent 返回 !defaultPrevented。
    render(<App />);
    const handle = await screen.findByRole("separator", { name: "调整右栏宽度" });

    const notPrevented = fireEvent.pointerDown(handle, { clientX: 1000 });
    expect(notPrevented).toBe(false); // 即 defaultPrevented === true

    // preventDefault 只阻默认行为，不影响拖拽本身：宽度照常跟随
    fireEvent.pointerMove(window, { clientX: 950 });
    await waitFor(() => expect(document.querySelector<HTMLElement>('[style*="--right-w"]')!.style.getPropertyValue("--right-w")).toBe("390px"));
    fireEvent.pointerUp(window);
  });

  test("拖拽结果写 localStorage，重挂载恢复上次宽度", async () => {
    const first = render(<App />);
    const handle = await screen.findByRole("separator", { name: "调整右栏宽度" });
    fireEvent.pointerDown(handle, { clientX: 1000 });
    fireEvent.pointerMove(window, { clientX: 860 }); // 340 + 140 = 480
    await waitFor(() => expect(rightW(first.container)).toBe("480px"));
    expect(localStorage.getItem("fieldwork.rightWidth")).toBe("480");
    first.unmount();

    const second = render(<App />);
    await screen.findByRole("separator", { name: "调整右栏宽度" });
    expect(rightW(second.container)).toBe("480px");
  });
});
