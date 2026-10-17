import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { WindowCard } from "../src/components/cards/WindowCard";

const GOLDEN = {
  windows: [
    { start: "2026-07-15", end: "2026-07-24", score: 29.97,
      reasons: ["价格谷值 560 元", "覆盖彝族火把节", "预算内 est 3790 元"],
      evidence: { price_score: 12.0, festival_score: 9.0, budget_score: 8.97,
                  flight_min: 650, hotel_min: 160, estimate: 3790, festivals: ["彝族火把节"] } },
  ],
  price_curve: {
    dates: ["2026-07-01", "2026-07-08", "2026-07-15", "2026-07-22", "2026-07-29"],
    flight_min: [700, 680, 650, 560, 600],
    hotel_min: [180, 170, 160, 130, 150],
  },
  festivals: [{ name: "彝族火把节", approx_dates: "农历六月廿四",
                approx_start: "2026-07-20", approx_end: "2026-07-22",
                relevance: "仪式密集观察窗口" }],
  budget_feasible: true,
  degraded: null,
};

describe("WindowCard", () => {
  test("渲染推荐窗口/分数/节庆/预算结论", () => {
    render(<WindowCard data={GOLDEN} />);
    expect(screen.getByText(/推荐 1：2026-07-15 → 2026-07-24/)).toBeInTheDocument();
    expect(screen.getByText("30.0 分")).toBeInTheDocument();  // 29.97.toFixed(1)
    // brief 修正：regex 与组件两处渲染（reasons li + festivals span）必然 multiple match，
    // 改精确字符串只命中 festivals 的 span（与 "30.0 分" 断言同风格，验证意图不变）
    expect(screen.getByText("彝族火把节")).toBeInTheDocument();
    expect(screen.getByText("预算内")).toBeInTheDocument();  // R07 三态（原 /预算可行/）
  });
  test("budget_feasible=null 渲染待核验文案（R07 三态）", () => {
    render(<WindowCard data={{ ...GOLDEN, budget_feasible: null }} />);
    expect(screen.getByText("待核验（仅已知小计）")).toBeInTheDocument();
  });
  test("budget_feasible=false 渲染超预算文案（R07 三态）", () => {
    render(<WindowCard data={{ ...GOLDEN, budget_feasible: false }} />);
    expect(screen.getByText("超预算")).toBeInTheDocument();
  });
  test("价格曲线含缺口点（null）不崩，仍渲染图", () => {
    render(<WindowCard data={{ ...GOLDEN,
      price_curve: { dates: ["07-01", "07-08", "07-15"],
                     flight_min: [700, null, 650], hotel_min: [180, null, 160] } }} />);
    expect(screen.getByRole("img", { name: /价格/ })).toBeInTheDocument();
  });
  test("data=null 不抛错（卡片永不白屏）", () => {
    expect(() => render(<WindowCard data={null} degraded="flyai_unavailable" />)).not.toThrow();
  });
});
