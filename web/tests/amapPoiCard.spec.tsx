import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AmapPoiCard } from "../src/components/cards/AmapPoiCard";
import { toolCardRegistry, toolVisibility } from "../src/components/cards/registry";

describe("AmapPoiCard", () => {
  it("列表渲染 name + extra 摘要，超出 LIST_LIMIT 注明总数", () => {
    const items = Array.from({ length: 8 }, (_, i) => ({
      name: `POI_${i}`, extra: { address: `路${i}` },
    }));
    render(<AmapPoiCard data={{ items, total: 250 }} />);
    expect(screen.getByText("POI_0")).toBeTruthy();
    expect(screen.queryByText("POI_6")).toBeNull();      // 只显前 LIST_LIMIT 条
    expect(screen.getByText(/共 250 条/)).toBeTruthy();   // 总数如实报告
  });
  it("空结果 → NoData", () => {
    render(<AmapPoiCard data={{ items: [], total: 0 }} />);
    expect(screen.getByText("工具未返回数据")).toBeTruthy();
  });
});

describe("toolVisibility 重分类（spec C3）", () => {
  it("检索类入证据 tab，内部地理管线隐藏", () => {
    expect(toolVisibility.search_literature).toBe("collapsed");   // full→collapsed（本轮 delta）
    expect(toolVisibility.amap_text_search).toBe("collapsed");
    for (const t of ["amap_geocode", "amap_regeocode", "amap_around_search",
                     "amap_direction_transit", "amap_weather"]) {
      expect(toolVisibility[t]).toBe("hidden");
    }
    expect(toolCardRegistry.amap_text_search).toBe(AmapPoiCard);
    // 既有分级不变（回归）
    expect(toolVisibility.profile_field_site).toBe("full");
    expect(toolVisibility.search_ai).toBe("collapsed");
    expect(toolVisibility.get_project_brief).toBe("hidden");
  });
});
