// web/tests/authDialog.spec.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/rest";
import { AuthDialog } from "../src/components/auth/AuthDialog";
import { useAuthStore } from "../src/stores/auth";

describe("AuthDialog", () => {
  // store 是模块级单例：失败路径测试会替换 login/register，测后还原防跨用例泄漏
  const origLogin = useAuthStore.getState().login;
  const origRegister = useAuthStore.getState().register;
  afterEach(() => {
    useAuthStore.setState({ login: origLogin, register: origRegister, user: null, expired: false });
  });

  it("渲染邮箱/密码输入与登录按钮", () => {
    render(<AuthDialog open onClose={() => {}} />);
    expect(screen.getByLabelText(/邮箱/)).toBeTruthy();
    expect(screen.getByLabelText(/密码/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /登录/ })).toBeTruthy();
  });

  it("Esc 关闭弹窗", () => {
    const onClose = vi.fn();
    render(<AuthDialog open onClose={onClose} />);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // 终审修项：ApiError.message 是后端响应体原文（JSON 字符串），人话化取
  // detail.message 内层文案，绝不把 {"detail":…} 原文甩用户脸上
  it("登录失败显示人话文案（detail.message），不透传 JSON 原文", async () => {
    useAuthStore.setState({
      login: vi.fn(async () => {
        throw new ApiError(401, JSON.stringify({
          detail: { code: "invalid_credentials", message: "邮箱或密码不正确" },
        }));
      }),
    });
    render(<AuthDialog open onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText(/邮箱/), { target: { value: "a@b.com" } });
    fireEvent.change(screen.getByLabelText(/密码/), { target: { value: "wrong-pass" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("邮箱或密码不正确");
    expect(alert).not.toHaveTextContent("{");
    expect(alert).not.toHaveTextContent("invalid_credentials");
  });
});
