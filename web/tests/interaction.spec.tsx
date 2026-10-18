import { describe, expect, test, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Composer, type TurnHandle } from "../src/components/chat/Composer";
import { PhaseBanner } from "../src/components/chat/PhaseBanner";
import { Timeline } from "../src/components/chat/Timeline";
import { LeftRail, DEFAULT_PHASES } from "../src/components/layout/LeftRail";
import { VersionSwitch } from "../src/components/proposal/VersionSwitch";
import { Tabs } from "../src/components/ui/Tabs";
import type { ProposalMeta } from "../src/api/rest";
import { useConversationStore } from "../src/stores/conversation";
import { useProjectStore } from "../src/stores/project";
import { useToolsStore } from "../src/stores/tools";
import { PHASE_TAB, useUiStore } from "../src/stores/ui";

/**
 * 交互规范回归（P0 可点性四连 + 空对话引导）：
 * 1. 选中态 = 实边框 + 色变 + IconCheck 勾选标记（Tabs / VersionSwitch）；
 * 2. 锁定项不伪装可点（LeftRail aria-disabled + title 解锁提示 + 点击守卫），
 *    可点条目只切右栏查看面板（PHASE_TAB 全映射，不推进阶段——产品红线）；
 * 3. PhaseBanner 尾文优先给解锁条件（PhaseItem.unlock），末阶段回落 desc；
 * 4. 空对话示例 chips：按当前阶段出题 → 点击经 ui store composerDraft 单次通道
 *    写入 Composer 输入框并聚焦，不自动发送（用户可改写后回车）。
 * （cursor 样式与 hover/focus-visible 视觉属 CSS 层，tokens.css 全局基线另由
 *  theme.spec/构建回归覆盖；此处只测行为与可访问属性。）
 */

/** mock 回合句柄：注入 Composer 后不触网，send 可断言"未自动发送" */
function mockTurn(): TurnHandle {
  return { send: vi.fn(), cancel: vi.fn(), busy: false, conflict: false };
}

beforeEach(() => {
  useConversationStore.getState().reset();
  useToolsStore.getState().reset();
  useProjectStore.getState().reset();
  useUiStore.getState().reset();
});

describe("选中态勾选标记（Tabs / VersionSwitch）", () => {
  test("Tabs：选中项含 IconCheck（svg）+ aria-selected，未选中不含", () => {
    render(
      <Tabs
        tabs={[
          { id: "basic-info", label: "基本信息" },
          { id: "sites", label: "田野点" },
        ]}
        active="basic-info"
        onChange={() => {}}
      />,
    );
    const selected = screen.getByRole("tab", { selected: true });
    expect(selected).toHaveTextContent("基本信息");
    expect(selected.querySelector("svg")).toBeInTheDocument();
    const unselected = screen.getByRole("tab", { selected: false });
    expect(unselected.querySelector("svg")).not.toBeInTheDocument();
  });

  test("VersionSwitch：选中 chip 含 IconCheck（svg）+ aria-pressed，未选中不含", () => {
    const versions: ProposalMeta[] = [
      { id: "v1", version: "draft" },
      { id: "v2", version: "final" },
    ];
    render(<VersionSwitch versions={versions} current="v2" onSelect={() => {}} />);
    const pressed = screen.getByRole("button", { pressed: true });
    expect(pressed).toHaveTextContent("final");
    expect(pressed.querySelector("svg")).toBeInTheDocument();
    expect(screen.getByRole("button", { pressed: false }).querySelector("svg")).not.toBeInTheDocument();
  });
});

describe("LeftRail 锁定态与点击语义（阶段推进红线）", () => {
  test("active 条目点击只上报 onSelect；锁定条目 aria-disabled + title 解锁提示且点击不响应", () => {
    const onSelect = vi.fn();
    render(<LeftRail phase="research_positioning" onSelect={onSelect} />);

    // A 为当前阶段：可点（App 接线后仅切右栏查看面板，不推进阶段）
    fireEvent.click(screen.getByRole("button", { name: /A · 研究定位/ }));
    expect(onSelect).toHaveBeenCalledWith("research_positioning");

    // B 锁定：不伪装可点，title 明示"完成什么才解锁"
    const lockedB = screen.getByRole("button", { name: /B · 田野点画像/ });
    expect(lockedB).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByTitle("完成「A · 研究定位」后解锁")).toBe(lockedB);
    fireEvent.click(lockedB);
    expect(onSelect).toHaveBeenCalledTimes(1); // 仍只有 A 那一次
  });

  test("未接 onSelect（纯展示场景）：active 条目同样 aria-disabled，点击不崩", () => {
    render(<LeftRail phase="research_positioning" />);
    const a = screen.getByRole("button", { name: /A · 研究定位/ });
    expect(a).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(a); // clickable=false 守卫，无 handler 可调
  });

  test("PHASE_TAB 四阶段全覆盖（含 research_positioning），App onSelect 接线不落空", () => {
    for (const p of DEFAULT_PHASES) {
      expect(PHASE_TAB[p.id]).toBeDefined();
    }
    expect(PHASE_TAB.research_positioning).toBe("basic-info");
  });
});

describe("PhaseBanner 尾文解锁条件提示", () => {
  test("非末阶段：尾文优先给下一阶段解锁条件（unlock）", () => {
    useProjectStore.getState().applyEvent({
      event: "phase_transition",
      from: "research_positioning",
      to: "research_positioning",
    });
    render(<PhaseBanner />);
    expect(screen.getByText(/本阶段条件达成后解锁：田野点画像/)).toBeInTheDocument();
  });

  test("末阶段（D 无 unlock）：回落阶段维度说明 desc", () => {
    useProjectStore.getState().applyEvent({
      event: "phase_transition",
      from: "itinerary_planning",
      to: "proposal_generation",
    });
    render(<PhaseBanner />);
    expect(screen.getByText(/英美式八章方案草稿/)).toBeInTheDocument();
    expect(screen.queryByText(/解锁/)).not.toBeInTheDocument();
  });
});

describe("空对话示例 chips → Composer 草稿通道", () => {
  test("空态按当前阶段出题，点击写入输入框并聚焦：通道清空、不自动发送", () => {
    useConversationStore.getState().hydrate([]);   // 已回填的空会话（非加载中），空态示例 chips 才渲染
    useProjectStore.getState().applyEvent({
      event: "phase_transition",
      from: "research_positioning",
      to: "itinerary_planning",
    });
    const turn = mockTurn();
    render(
      <>
        <Timeline />
        <Composer turn={turn} />
      </>,
    );

    // itinerary_planning 阶段的示例问题在位（该阶段右栏面板对应预算/行程能力）
    fireEvent.click(screen.getByText("按 10 天时间窗排一版行程草案"));

    // 单次通道：写入即被 Composer 消费清回 null
    expect(useUiStore.getState().composerDraft).toBeNull();
    // 输入框值 = chip 文案并聚焦；未自动发送（用户可改写后回车）
    const box = screen.getByRole("textbox");
    expect(box).toHaveValue("按 10 天时间窗排一版行程草案");
    expect(document.activeElement).toBe(box);
    expect(turn.send).not.toHaveBeenCalled();
  });

  test("未知阶段回落 A 阶段示例（研究定位）", () => {
    useConversationStore.getState().hydrate([]);   // 已回填的空会话，空态示例 chips 才渲染
    useProjectStore.setState({ phase: "weird_phase" });
    render(<Timeline />);
    expect(
      screen.getByText("我想研究村落宗族组织的变迁，帮我定位学科与核心研究问题"),
    ).toBeInTheDocument();
  });
});
