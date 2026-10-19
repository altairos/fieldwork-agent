import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ToolActivityStrip } from "../src/components/chat/ToolActivityStrip";
import { useToolsStore } from "../src/stores/tools";

/**
 * 活动条只显示「活的和有问题的」，其中 hidden 级（内部状态写入 + 内部地理管线）
 * 连失败都不能冒出来（评审 P1-2 / spec C3）。原先的判定是硬编码排除
 * `update_research_context` 一个名字，于是 `amap_geocode` 失败会显示
 * 「地理编码 · 调用失败」——用户看不到这张卡的上下文，只觉得系统在报莫名其妙的错。
 */

function start(callId: string, tool: string) {
  useToolsStore.getState().applyEvent({
    event: "tool_started", call_id: callId, tool, args: {}, purpose: "",
  } as never);
}
function fail(callId: string) {
  useToolsStore.getState().applyEvent({
    event: "tool_completed", call_id: callId, ok: false, data: null,
    error: { code: "internal_error", message: "boom" },
  } as never);
}

beforeEach(() => useToolsStore.getState().reset());

describe("ToolActivityStrip 的 hidden 过滤（P1-2）", () => {
  it("hidden 工具失败不冒行（spec C3：成功与失败均不显示）", () => {
    start("c1", "amap_geocode");
    fail("c1");
    const { container } = render(<ToolActivityStrip />);
    expect(screen.queryByText("地理编码")).toBeNull();
    expect(container.firstChild).toBeNull();          // 全被过滤时不留空壳
  });

  it("hidden 工具进行中也不显示", () => {
    start("c2", "get_project_brief");
    render(<ToolActivityStrip />);
    expect(screen.queryByText("项目摘要查询")).toBeNull();
  });

  it("可见工具的失败照常显示（异常不得被一并吞掉）", () => {
    start("c3", "search_hotels");
    fail("c3");
    render(<ToolActivityStrip />);
    expect(screen.getByText("酒店搜索")).toBeInTheDocument();
    expect(screen.getByText(/调用失败|未取得数据/)).toBeInTheDocument();
  });

  it("同一次渲染里 hidden 与可见工具混排：只留可见的那条", () => {
    start("c4", "amap_weather");
    fail("c4");
    start("c5", "search_literature");
    fail("c5");
    render(<ToolActivityStrip />);
    expect(screen.queryByText("天气查询")).toBeNull();
    expect(screen.getByText("文献检索")).toBeInTheDocument();
  });
});
