import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { LastMileCard } from "../src/components/cards/LastMileCard";
import { ToolCardFallback } from "../src/components/cards/ToolCardFallback";
import { getToolCard } from "../src/components/cards/registry";
import { TOOL_VISIBILITY } from "../src/lib/toolMeta";

/**
 * plan_last_mile 专属卡（2026-10-20，生产会话实测缺口）：
 * 该能力此前未注册，时间线落 ToolCardFallback「未适配的结构化视图」。
 * payload 形状取自生产会话 field_20261020_191312 的真实返回。
 */

const GEO_HUB = {
  location: "102.264166,27.894410",
  province: "四川省",
  city: "凉山彝族自治州",
  district: "西昌市",
  township: "",
  formatted_address: "四川省凉山彝族自治州西昌市",
};
const GEO_SITE = {
  location: "103.132359,28.328591",
  province: "四川省",
  city: "凉山彝族自治州",
  district: "美姑县",
  township: "",
  formatted_address: "四川省凉山彝族自治州美姑县",
};

describe("plan_last_mile 接线", () => {
  test("注册表命中 LastMileCard（不再落 Fallback），可见性为 full", () => {
    expect(getToolCard("plan_last_mile")).toBe(LastMileCard);
    expect(getToolCard("plan_last_mile")).not.toBe(ToolCardFallback);
    expect(TOOL_VISIBILITY.plan_last_mile).toBe("full");
  });
});

describe("LastMileCard 呈现", () => {
  test("无公交方案：两端行政区划可读 + 内核 degraded 原文转述 + 下一步建议", () => {
    render(
      <LastMileCard
        data={{
          hub: GEO_HUB,
          site: GEO_SITE,
          options: { items: [], total: 0, cheapest: null, dearest: null },
          live_refs: [],
        }}
        degraded="高德班次日期仅支持 3 天内，已按当日/实时班次查询"
      />,
    );
    expect(screen.getByText("末端接驳")).toBeInTheDocument();
    expect(screen.getByText("西昌市 → 美姑县")).toBeInTheDocument();
    expect(screen.getByText("四川省凉山彝族自治州西昌市")).toBeInTheDocument();
    expect(screen.getByText("四川省凉山彝族自治州美姑县")).toBeInTheDocument();
    // 内核附言原文（不加工、不概括）
    expect(screen.getByText(/高德班次日期仅支持 3 天内/)).toBeInTheDocument();
    expect(screen.getByText(/可改用城际班车\/包车等公路方式衔接/)).toBeInTheDocument();
    expect(screen.getByText("未取得方案")).toBeInTheDocument();
  });

  test("有公交方案：线路 / 时长换乘 / 票价逐条呈现（null 票价落「暂无报价」）", () => {
    render(
      <LastMileCard
        data={{
          hub: GEO_HUB,
          site: GEO_SITE,
          options: {
            items: [
              {
                name: "西昌汽车客运站 → 美姑",
                price: 45,
                price_raw: "¥45",
                jump_url: null,
                pic_url: null,
                extra: {},
                schedule: { duration_minutes: 180, stops: 0, stops_kind: "transfers" },
              },
              {
                name: "西昌 → 昭觉 → 美姑",
                price: null,
                price_raw: null,
                jump_url: null,
                pic_url: null,
                extra: {},
                schedule: { duration_minutes: 240, stops: 1, stops_kind: "transfers" },
              },
            ],
            total: 2,
            cheapest: 45,
            dearest: null,
          },
          live_refs: [],
        }}
      />,
    );
    expect(screen.getByText("西昌汽车客运站 → 美姑")).toBeInTheDocument();
    expect(screen.getByText("3 小时 · 直达")).toBeInTheDocument();
    expect(screen.getByText("4 小时 · 换乘 1 次")).toBeInTheDocument();
    expect(screen.getByText("¥45")).toBeInTheDocument();
    expect(screen.getByText("暂无报价")).toBeInTheDocument();   // 绝不 ¥0
  });

  test("无数据（失败/降级）不白屏：给出可操作提示", () => {
    render(<LastMileCard data={null} />);
    expect(screen.getByText("工具未返回数据")).toBeInTheDocument();
  });
});
