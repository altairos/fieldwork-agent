// web/tests/apiKeyCard.spec.ts — ApiKeySection 飞猪 Key「可选高级项」收纳（计费核验结论落地）
// + 套餐端点选择（sk-sp- 套餐 Key 配专属 OpenAI 兼容端点，否则默认端点 401）
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ApiKeySection } from "../src/components/settings/ApiKeyCard";
import { authHeaders } from "../src/lib/keys";

// mock 成产品现实形态：服务端已配飞猪/高德共享 Key、百炼靠页面 Key
vi.mock("../src/api/rest", () => ({
  rest: {
    settingsStatus: async () => ({ dashscope_configured: false, flyai_configured: true, amap_configured: true }),
    health: async () => ({ status: "ok", auth_required: false }),
  },
}));

describe("ApiKeySection — 飞猪/高德 Key 高级项收纳", () => {
  beforeEach(() => localStorage.clear());

  it("默认收起：百炼输入可见，飞猪/高德输入不可见，飞猪来源徽标仍外显", async () => {
    render(<ApiKeySection />);
    expect(await screen.findByText("API 密钥")).toBeInTheDocument();
    expect(screen.getByLabelText(/百炼 API Key/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/飞猪 API Key/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/高德 API Key/)).not.toBeInTheDocument();
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

  it("展开高级项：高德 Key 输入出现，填写保存后写入 localStorage 并注入请求头", async () => {
    render(<ApiKeySection />);
    await screen.findByText("API 密钥");
    fireEvent.click(screen.getByRole("button", { name: /高级选项/ }));
    const amapInput = screen.getByLabelText(/高德 API Key/);
    fireEvent.change(amapInput, { target: { value: "ak-e2e-456" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(localStorage.getItem("fieldwork.amap_key")).toBe("ak-e2e-456");
    expect(authHeaders()["X-Amap-Key"]).toBe("ak-e2e-456");
  });
});

describe("ApiKeySection — 套餐端点选择（sk-sp- 套餐 Key）", () => {
  beforeEach(() => localStorage.clear());

  it("输入 sk-sp- 前缀自动展开高级项并出现套餐类型选择与警告", async () => {
    render(<ApiKeySection />);
    await screen.findByText("API 密钥");
    fireEvent.change(screen.getByLabelText(/百炼 API Key/), {
      target: { value: "sk-sp-abc123" },
    });
    // 自动展开高级项：套餐类型选择可见
    expect(screen.getByLabelText(/百炼套餐类型/)).toBeInTheDocument();
    // 未选端点时警告提示（cinnabar）
    expect(screen.getByText(/检测到套餐 Key/)).toBeInTheDocument();
  });

  it("选择 Coding 套餐保存：plan 写入 localStorage，警告消失", async () => {
    render(<ApiKeySection />);
    await screen.findByText("API 密钥");
    fireEvent.change(screen.getByLabelText(/百炼 API Key/), {
      target: { value: "sk-sp-abc123" },
    });
    fireEvent.change(screen.getByLabelText(/百炼套餐类型/), {
      target: { value: "coding" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(localStorage.getItem("fieldwork.dashscope_plan")).toBe("coding");
    expect(localStorage.getItem("fieldwork.dashscope_key")).toBe("sk-sp-abc123");
    expect(screen.queryByText(/检测到套餐 Key/)).not.toBeInTheDocument();
  });

  it("authHeaders 携带 X-DashScope-Plan；换回普通 sk- key 自动回落默认端点", async () => {
    const { unmount } = render(<ApiKeySection />);
    await screen.findByText("API 密钥");
    fireEvent.change(screen.getByLabelText(/百炼 API Key/), {
      target: { value: "sk-sp-abc123" },
    });
    fireEvent.change(screen.getByLabelText(/百炼套餐类型/), {
      target: { value: "token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已保存");
    expect(authHeaders()["X-DashScope-Plan"]).toBe("token");
    // 换回普通 key：plan 自动重置，请求头不再带端点
    unmount();
    localStorage.clear();
    render(<ApiKeySection />);
    await screen.findByText("API 密钥");
    fireEvent.change(screen.getByLabelText(/百炼 API Key/), {
      target: { value: "sk-normal-123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已保存");
    expect(authHeaders()["X-DashScope-Plan"]).toBeUndefined();
    expect(localStorage.getItem("fieldwork.dashscope_plan")).toBeNull();
  });
});
