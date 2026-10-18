import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { LivingCostCard } from "../src/components/cards/LivingCostCard";

const DATA = {
  settlement_type: "village", board_mode: "self_cook", people: 1, days: 60,
  caliber: "村内自炊 · 1 人 × 60 天",
  items: [
    { name: "餐饮", per_day_low: 12, per_day_high: 20,
      amount_low: 720, amount_high: 1200, unit: "元/人·天×人数×天数" },
    { name: "市内交通", per_day_low: 0, per_day_high: 5,
      amount_low: 0, amount_high: 300, unit: "元/人·天×人数×天数" },
  ],
  total_low: 720, total_high: 1500,
  assumptions: ["自炊按在家做饭、不含酒水"],
  basis: "云南农村居民人均食品烟酒 5,351 元/年",
  source: "estimate", coverage: "estimate",
};

describe("LivingCostCard", () => {
  test("渲染口径、明细区间与合计", () => {
    render(<LivingCostCard data={DATA} />);
    expect(screen.getByText(/村内自炊 · 1 人 × 60 天/)).toBeInTheDocument();
    expect(screen.getByText("¥720–¥1200")).toBeInTheDocument();
    expect(screen.getByText("¥0–¥300")).toBeInTheDocument();
    expect(screen.getByText(/合计.*¥720–¥1500/)).toBeInTheDocument();
  });

  test("渲染假设与依据", () => {
    render(<LivingCostCard data={DATA} />);
    expect(screen.getByText(/不含酒水/)).toBeInTheDocument();
    expect(screen.getByText(/5,351 元\/年/)).toBeInTheDocument();
  });

  test("空 data 落空态", () => {
    render(<LivingCostCard data={null} />);
    expect(screen.getByText(/未取得生活成本估算/)).toBeInTheDocument();
  });
});
