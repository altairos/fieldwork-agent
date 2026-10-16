// web/tests/apiKeyCard.spec.ts — ApiKeySection 飞猪 Key「可选高级项」收纳（计费核验结论落地）
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ApiKeySection } from "../src/components/settings/ApiKeyCard";

// mock 成产品现实形态：服务端已配飞猪共享 Key、百炼靠页面 Key
vi.mock("../src/api/rest", () => ({
  rest: {
    settingsStatus: async () => ({ dashscope_configured: false, flyai_configured: true }),
    health: async () => ({ status: "ok", auth_required: false }),
  },
}));

describe("ApiKeySection — 飞猪 Key 高级项收纳", () => {
  beforeEach(() => localStorage.clear());

  it("默认收起：百炼输入可见，飞猪输入不可见，飞猪来源徽标仍外显", async () => {
    render(<ApiKeySection />);
    expect(await screen.findByText("API 密钥")).toBeInTheDocument();
    expect(screen.getByLabelText(/百炼 API Key/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/飞猪 API Key/)).not.toBeInTheDocument();
    // 折叠态下飞猪徽标在折叠行外显（用户不展开也能知道服务端已配）
    expect(screen.getByText("服务端已配")).toBeInTheDocument();
  });

  it("展开高级项：飞猪输入出现，填写保存后写入 localStorage", async () => {
    render(<ApiKeySection />);
    await screen.findByText("API 密钥");
    const toggle = screen.getByRole("button", { name: /高级选项/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const flyaiInput = screen.getByLabelText(/飞猪 API Key/);
    fireEvent.change(flyaiInput, { target: { value: "sk-fly-e2e" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(localStorage.getItem("fieldwork.flyai_key")).toBe("sk-fly-e2e");
  });
});
