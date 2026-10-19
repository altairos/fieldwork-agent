import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AiSearchCard } from "../src/components/cards/AiSearchCard";

describe("AiSearchCard", () => {
  it("answer 渲染为 Markdown（链接可点）", () => {
    render(<AiSearchCard data={{ answer: "## 民宿推荐\n- [甲处](https://x.example)", total: 0 }} />);
    expect(screen.getByRole("heading", { name: "民宿推荐" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "甲处" })).toBeTruthy();
  });
  it("空 answer + total=0 → NoData，不白屏", () => {
    render(<AiSearchCard data={{ items: [], total: 0 }} />);
    expect(screen.getByText("工具未返回数据")).toBeTruthy();
  });
  it("data 为 null → NoData（容错读法）", () => {
    render(<AiSearchCard data={null} />);
    expect(screen.getByText("工具未返回数据")).toBeTruthy();
  });
  it("dict 形态（itemList）无 answer 有条目 → 回退 PoiCard 列表渲染，不落 NoData（终审 W2，A3/A4）", () => {
    render(
      <AiSearchCard
        data={{ items: [{ name: "古城民宿", price: 150 }, { name: "喜洲客栈", price: 180 }],
               total: 2, cheapest: 150, dearest: 180 }}
      />,
    );
    expect(screen.getByText("古城民宿")).toBeTruthy();          // PoiCard 路径渲染条目
    expect(screen.getByText("喜洲客栈")).toBeTruthy();
    expect(screen.getByText("最低 ¥150 · 最高 ¥180")).toBeTruthy();  // SearchResultCard 摘要行
    expect(screen.queryByText("工具未返回数据")).toBeNull();    // 不落 NoData
    expect(screen.queryByText("本次 AI 检索未返回答案文本。")).toBeNull();
  });
});
