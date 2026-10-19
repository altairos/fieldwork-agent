import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { LiteratureCard } from "../src/components/cards/LiteratureCard";

describe("LiteratureCard 数量与可追溯性", () => {
  test("旧total上万也只报告实际返回候选数，命中数在检索记录中单独标注", () => {
    render(<LiteratureCard data={{
      total: 24000,
      items: [{ title: "Kinship and change", authors: ["Researcher"], year: 2024, doi: "10.1/kinship" }],
      query_reports: [{ label: "核心", query: '"kinship"', matched_total: 24000, retrieved_count: 1 }],
    }} />);
    expect(screen.getByText("返回 1 篇候选 · 标题摘要初筛")).toBeInTheDocument();
    fireEvent.click(screen.getByText("文献检索"));
    expect(screen.getByRole("link", { name: "Kinship and change" })).toHaveAttribute("href", "https://doi.org/10.1/kinship");
    expect(screen.getByText(/无摘要.*待核验/)).toBeVisible();
    fireEvent.click(screen.getByText("检索式与每路数据库命中（可能重叠）"));
    expect(screen.getByText(/数据库命中 24000.*取回 1 篇/)).toBeVisible();
  });

  test("空结果展示错误与中文补检入口", () => {
    render(<LiteratureCard data={{ items: [], chinese_sources: [
      { label: "PubScholar", url: "https://pubscholar.cn/search?q=kinship" },
    ] }} error={{ message: "OpenAlex 未命中任何文献" }} />);
    fireEvent.click(screen.getByText("文献检索"));
    expect(screen.getByText("OpenAlex 未命中任何文献")).toBeVisible();
    expect(screen.getByRole("link", { name: "PubScholar" })).toBeVisible();
  });

  test("畸形结果与非学术跳转不会产生危险链接", () => {
    render(<LiteratureCard data={{ items: [null, { title: "Bad link", id: "javascript:alert(1)" }] }} />);
    fireEvent.click(screen.getByText("文献检索"));
    expect(screen.getByText("Bad link")).toBeVisible();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
