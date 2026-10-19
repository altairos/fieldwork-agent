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
});
