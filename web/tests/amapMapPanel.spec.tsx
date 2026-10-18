// N6（PoC2 §5.2）：AmapMapPanel 降级链 + useAmapLoader 状态机。
// jsdom 无 WebGL：@amap/amap-jsapi-loader 全程 vi.mock，只验证调用契约
// （load 参数 / Map·Marker·Polyline 构造），不真渲染地图。
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const loadMock = vi.fn();
vi.mock("@amap/amap-jsapi-loader", () => ({
  default: { load: (...args: unknown[]) => loadMock(...args) },
}));

// AMap 命名空间替身：记录构造调用供断言（this 上挂 destroy 供卸载清理）
const made = {
  maps: [] as Array<{ el: HTMLElement; opts: Record<string, unknown> }>,
  markers: [] as Array<Record<string, unknown>>,
  polylines: [] as Array<Record<string, unknown>>,
};
const destroyMock = vi.fn();
// Map 实例方法替身：真实 JS API 2.0 的 Map 有 add/destroy——组件写 map.add?.(overlay)
// 防御可选调用，若 mock 缺 add，可选链短路连参数（new Marker/Polyline）都不求值
const addMock = vi.fn();
const AMapMock = {
  Map: vi.fn(function (this: { add?: unknown; destroy?: unknown }, el: HTMLElement, opts: Record<string, unknown>) {
    made.maps.push({ el, opts });
    this.add = addMock;
    this.destroy = destroyMock;
  }),
  Marker: vi.fn(function (this: unknown, opts: Record<string, unknown>) {
    made.markers.push(opts);
  }),
  Polyline: vi.fn(function (this: unknown, opts: Record<string, unknown>) {
    made.polylines.push(opts);
  }),
};

/** 组件模块级 settings promise 有缓存：每用例 resetModules 拿干净实例。 */
async function freshPanel() {
  vi.resetModules();
  return await import("../src/components/cards/AmapMapPanel");
}

function stubSettings(body: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async () =>
    ({ ok: true, json: async () => body } as unknown as Response)));
}

const NO_KEY = { amap_js_key: "", amap_js_security: "" };
const HAS_KEY = { amap_js_key: "jsk-1", amap_js_security: "jsc-1" };

beforeEach(() => {
  loadMock.mockReset();
  addMock.mockReset();
  destroyMock.mockReset();
  made.maps.length = 0;
  made.markers.length = 0;
  made.polylines.length = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useAmapLoader 状态机（N6 §5.1）", () => {
  test("服务端未配 JS Key → unavailable（面板走降级卡片）", async () => {
    stubSettings(NO_KEY);
    const { useAmapLoader } = await freshPanel();
    function Probe() {
      const s = useAmapLoader();
      return <div data-testid="state">{s.state}</div>;
    }
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("unavailable"));
  });

  test("有 Key → loader.load({key, securityJsCode}) → ready", async () => {
    stubSettings(HAS_KEY);
    loadMock.mockResolvedValue(AMapMock);
    const { useAmapLoader } = await freshPanel();
    function Probe() {
      const s = useAmapLoader();
      return <div data-testid="state">{s.state}</div>;
    }
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("ready"));
    expect(loadMock).toHaveBeenCalledWith({ key: "jsk-1", securityJsCode: "jsc-1" });
  });

  test("loader 失败 → error（不回退 unavailable：Key 在位是配置事实）", async () => {
    stubSettings(HAS_KEY);
    loadMock.mockRejectedValue(new Error("JSAPI_INVALID_KEY"));
    const { useAmapLoader } = await freshPanel();
    function Probe() {
      const s = useAmapLoader();
      return <div data-testid="state">{s.state}</div>;
    }
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("error"));
  });

  test("settings 请求失败（网络缺席）→ unavailable 而非挂起", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("network"); }));
    const { useAmapLoader } = await freshPanel();
    function Probe() {
      const s = useAmapLoader();
      return <div data-testid="state">{s.state}</div>;
    }
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("unavailable"));
  });
});

describe("AmapMapPanel 降级链（N6 §5.2）", () => {
  test("无 Key → 降级卡片：坐标与设施清单仍可读", async () => {
    stubSettings(NO_KEY);
    const { AmapMapPanel } = await freshPanel();
    render(
      <AmapMapPanel
        center="100.196,25.047"
        markers={[{ location: "100.20,25.05", title: "喜洲卫生院", kind: "卫生院 诊所" }]}
      />,
    );
    expect(await screen.findByText(/未配置高德 JS API Key/)).toBeInTheDocument();
    expect(screen.getByText("100.196,25.047")).toBeInTheDocument();
    expect(screen.getByText("喜洲卫生院")).toBeInTheDocument();
  });

  test("有 Key → 挂载地图容器：center 居中 + Marker×2 + 接驳两点直线", async () => {
    stubSettings(HAS_KEY);
    loadMock.mockResolvedValue(AMapMock);
    const { AmapMapPanel } = await freshPanel();
    render(
      <AmapMapPanel
        center="100.196,25.047"
        height={180}
        markers={[
          { location: "100.196,25.047", title: "喜洲镇" },
          { location: "100.20,25.05", title: "喜洲卫生院", kind: "卫生院 诊所" },
        ]}
        line={["100.071,25.606", "100.196,25.047"]}
      />,
    );
    await waitFor(() => expect(made.maps).toHaveLength(1));
    expect(made.maps[0].opts.center).toEqual([100.196, 25.047]);
    expect(made.markers).toHaveLength(2);
    expect(made.polylines).toHaveLength(1);
    expect(made.polylines[0].path).toEqual([[100.071, 25.606], [100.196, 25.047]]);
  });

  test("loader 失败 → 降级卡片（不白屏）", async () => {
    stubSettings(HAS_KEY);
    loadMock.mockRejectedValue(new Error("network"));
    const { AmapMapPanel } = await freshPanel();
    render(<AmapMapPanel center="100.196,25.047" markers={[]} />);
    expect(await screen.findByText(/地图加载失败/)).toBeInTheDocument();
    expect(screen.getByText("100.196,25.047")).toBeInTheDocument();
  });

  test("无 center（geo 缺段）→ 文字提示，不调 loader", async () => {
    stubSettings(HAS_KEY);
    loadMock.mockResolvedValue(AMapMock);
    const { AmapMapPanel } = await freshPanel();
    render(<AmapMapPanel markers={[]} />);
    expect(await screen.findByText(/暂无定位坐标/)).toBeInTheDocument();
    expect(loadMock).not.toHaveBeenCalled();
  });
});
