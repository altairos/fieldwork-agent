import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  isHiddenTool, toolLabel, TOOL_LABELS, TOOL_VISIBILITY, visibilityOf,
} from "../src/lib/toolMeta";

/**
 * toolMeta 是工具元数据的唯一来源（评审 P1-3），这里钉三件事：
 * 1. 默认可见性 = full（新能力先可见，再人工分级）；
 * 2. hidden 集合与 spec C3 对齐（内部状态读写 + 内部地理管线）；
 * 3. **依赖方向**：工具元数据不得反向依赖组件层——这是 P1-3 的原问题，
 *    一旦有人把 import 加回去，单测导入 store 就会连带加载整棵卡片树。
 */

// 用 cwd 而不是 import.meta.url：vite/vitest 的 CJS shim 下 import.meta.url 可能为 undefined
const src = (p: string) => readFileSync(resolve(process.cwd(), "src", p), "utf8");

describe("toolMeta 可见性与标签", () => {
  it("未登记工具默认 full（不因缺登记而消失）", () => {
    expect(visibilityOf("brand_new_capability")).toBe("full");
    expect(visibilityOf("get_project_brief")).toBe("hidden");
    expect(visibilityOf("search_hotels")).toBe("collapsed");
    expect(visibilityOf("compose_itinerary")).toBe("full");
  });

  it("hidden 集合 = 内部状态读写 + 内部地理管线（spec C3：成功与失败均不显示）", () => {
    const hidden = Object.entries(TOOL_VISIBILITY)
      .filter(([, v]) => v === "hidden")
      .map(([k]) => k)
      .sort();
    expect(hidden).toEqual([
      "amap_around_search", "amap_direction_transit", "amap_geocode", "amap_regeocode",
      "amap_weather", "get_project_brief", "update_research_context",
    ]);
    // 活动条/时间线的判定入口与表同源（不允许各自维护一份名单）
    expect(hidden.every(isHiddenTool)).toBe(true);
    expect(isHiddenTool("amap_text_search")).toBe(false);  // 用户可见的地点检索
  });

  it("中文名兜底回落原始工具名（不抛错、不臆造）", () => {
    expect(toolLabel("search_hotels")).toBe("酒店搜索");
    expect(toolLabel("unknown_tool")).toBe("unknown_tool");
    // 标签表覆盖全部可见性登记项（漏一个就会在界面上露英文名）
    for (const tool of Object.keys(TOOL_VISIBILITY)) {
      expect(TOOL_LABELS[tool], `缺少中文名：${tool}`).toBeTruthy();
    }
  });
});

describe("依赖方向护栏（评审 P1-3 的回归钉）", () => {
  it("lib/toolMeta.ts 是零依赖纯数据", () => {
    expect(src("lib/toolMeta.ts")).not.toMatch(/^import\s/m);
  });

  it("stores 层不 import 组件层（否则单测导入 store 会拉起整棵卡片树）", () => {
    const ui = src("stores/ui.ts");
    expect(ui).not.toMatch(/from ["'][^"']*components\//);
    expect(ui).toMatch(/from ["']\.\.\/lib\/toolMeta["']/);
  });
});
