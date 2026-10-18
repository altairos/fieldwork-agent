import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { Price } from "../src/components/ui/Price";
import { Chip } from "../src/components/ui/Chip";
import { Badge } from "../src/components/ui/Badge";
import { ProgressBar } from "../src/components/ui/ProgressBar";
import { Tabs } from "../src/components/ui/Tabs";

describe("Price 不失真", () => {
  test("有值显示 ¥ + 来源", () => {
    render(<Price value={180} source="search" />);
    expect(screen.getByText("¥180")).toBeInTheDocument();
    expect(screen.getByText("实时搜索")).toBeInTheDocument();
  });
  test("null 显示暂无报价(非 ¥0/¥null)", () => {
    render(<Price value={null} />);
    expect(screen.getByText("暂无报价")).toBeInTheDocument();
    expect(screen.queryByText(/¥/)).not.toBeInTheDocument();
  });
});

describe("Chip/Badge", () => {
  test("Chip 渲染文本", () => {
    render(<Chip tone="cinnabar">人类学</Chip>);
    expect(screen.getByText("人类学")).toBeInTheDocument();
  });
  test("Badge 高亮当前阶段", () => {
    render(<Badge phase="A" active />);
    expect(screen.getByText("A")).toBeInTheDocument();
  });
});

describe("ProgressBar/Tabs", () => {
  test("ProgressBar 渲染四段", () => {
    render(<ProgressBar phase={1} segments={["A", "B", "C", "D"]} />);
    expect(screen.getByText("A")).toBeInTheDocument();
    expect(screen.getByText("D")).toBeInTheDocument();
  });
  test("Tabs 点击切换", () => {
    let active = "basic-info";
    render(
      <Tabs
        tabs={[
          { id: "basic-info", label: "基本信息" },
          { id: "sites", label: "田野点" },
        ]}
        active={active}
        onChange={(t) => {
          active = t;
        }}
      />,
    );
    expect(screen.getByText("基本信息")).toBeInTheDocument();
  });
});
