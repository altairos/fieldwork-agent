import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { SearchItemRow } from "../src/components/cards/SearchResultCard";
import { WindowCard } from "../src/components/cards/WindowCard";
import { LodgingComparison } from "../src/components/cards/LodgingComparison";

describe("旅行证据展示", () => {
  test("住宿比较明确展示情景估算、通勤时间及待核实条件", () => {
    render(<LodgingComparison data={[{ name: "近舍", cost_estimate: 864,
      commute_minutes_per_visit_day: 20, quote_coverage: "estimate",
      constraints: { status: "unknown", missing_fields: ["连续房态"] } }]} />);
    expect(screen.getByText(/房费与通勤合计情景估算/)).toBeVisible();
    expect(screen.getByText(/每日往返约 20 分钟/)).toBeVisible();
    expect(screen.getByText("连续房态")).toBeVisible();
  });
  test("显示来源实际返回的到达时间、枢纽和缓存采样时间", () => {
    render(<ul><SearchItemRow item={{
      name: "班次", price: 100, price_raw: null, jump_url: null, pic_url: null, extra: {},
      schedule: { dep_time: "08:30", arr_time: "10:05", arrival_place: "青山机场" },
      evidence: { source: "flyai", source_item_id: "", query_conditions: {}, fetched_at: "2026-10-19T10:00:00Z",
        source_updated_at: "", cache_hit: true, source_ref: "fixture:quote", references: [], missing_fields: [] },
    }} /></ul>);
    expect(screen.getByText(/10:05 青山机场/)).toBeVisible();
    expect(screen.getByText(/缓存采样 2026-10-19T10:00:00Z/)).toBeVisible();
  });

  test("未知活动日期和采样预算不显示成已确认结论", () => {
    render(<WindowCard data={{ budget_feasible: null, windows: [],
      festivals: [{ name: "节庆套餐线索", date_status: "unverified" }] }} />);
    expect(screen.getByText(/活动线索，日期待核实/)).toBeInTheDocument();
    expect(screen.queryByText("预算内")).not.toBeInTheDocument();
  });
});
