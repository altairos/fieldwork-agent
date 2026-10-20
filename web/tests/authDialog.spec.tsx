// web/tests/authDialog.spec.tsx — 登录弹层（AuthDialog）改版：视觉重构不直接断言样式，
// 功能增强逐项测：密码显隐 / 记住邮箱 / Enter 提交 / busy 文案 / 高级密钥折叠区
// （收起不挂载 ApiKeySection —— 与弹层内既有查询隔离，防选择器互相污染）。
//
// 选择器消歧（getByLabelText 字符串默认全等）：显隐钮 aria-label「显示密码/隐藏密码」
// 与密码框 aria-label「密码」构成子串关系，密码框一律 "密码" 全等查询，显隐钮按
// 当前态的完整 label 查询。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/rest";
import { AuthDialog } from "../src/components/auth/AuthDialog";
import { useAuthStore } from "../src/stores/auth";

// 高级折叠区挂载 ApiKeySection → 其 useEffect 查 /settings/status 与 /health：
// 仅替换这两个方法（stores/auth 顶层还消费 setOnSessionExpired 等导出，须保留真实），
// 其余（ApiError 等）走原实现，humanError instanceof 判定不变。
vi.mock("../src/api/rest", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api/rest")>();
  return {
    ...actual,
    rest: {
      ...actual.rest,
      settingsStatus: async () => ({ dashscope_configured: true, flyai_configured: true, amap_configured: true }),
      health: async () => ({ status: "ok", auth_required: false }),
    },
  };
});

describe("AuthDialog", () => {
  // store 是模块级单例：失败路径测试会替换 login/register，测后还原防跨用例泄漏
  const origLogin = useAuthStore.getState().login;
  const origRegister = useAuthStore.getState().register;
  afterEach(() => {
    useAuthStore.setState({ login: origLogin, register: origRegister, user: null, expired: false });
    localStorage.clear(); // 记住邮箱用例写 localStorage，清场防跨用例误命中
  });

  it("渲染邮箱/密码输入与登录按钮", () => {
    render(<AuthDialog open onClose={() => {}} />);
    expect(screen.getByLabelText("邮箱")).toBeTruthy();
    expect(screen.getByLabelText("密码")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "登录" })).toBeTruthy();
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
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "a@b.com" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong-pass" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("邮箱或密码不正确");
    expect(alert).not.toHaveTextContent("{");
    expect(alert).not.toHaveTextContent("invalid_credentials");
  });

  it("密码显隐切换：type password↔text，aria-pressed 与显隐钮 label 联动", () => {
    render(<AuthDialog open onClose={() => {}} />);
    const toggle = screen.getByRole("button", { name: "显示密码" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(screen.getByLabelText("密码")).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "隐藏密码" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "隐藏密码" }));
    expect(screen.getByLabelText("密码")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "显示密码" })).toBeTruthy();
  });

  it("记住邮箱勾选框仅登录模式显示（注册模式不出现）", () => {
    render(<AuthDialog open onClose={() => {}} />);
    expect(screen.getByLabelText("记住邮箱")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /没有账号/ }));
    expect(screen.queryByLabelText("记住邮箱")).toBeNull();
  });

  it("登录成功且勾选记住邮箱：邮箱写入 localStorage（永不存密码）", async () => {
    const login = vi.fn(async () => {});
    useAuthStore.setState({ login });
    const onClose = vi.fn();
    render(<AuthDialog open onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "a@b.com" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "pw-123" } });
    fireEvent.click(screen.getByLabelText("记住邮箱"));
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(login).toHaveBeenCalledWith("a@b.com", "pw-123");
    expect(localStorage.getItem("fieldwork.remembered_email")).toBe("a@b.com");
    expect(localStorage.getItem("fieldwork.remembered_password")).toBeNull();
  });

  it("登录成功但未勾选：清除已存邮箱（预填旧值成功登录后不残留）", async () => {
    localStorage.setItem("fieldwork.remembered_email", "old@x.com");
    useAuthStore.setState({ login: vi.fn(async () => {}) });
    const onClose = vi.fn();
    render(<AuthDialog open onClose={onClose} />);
    expect(screen.getByLabelText("邮箱")).toHaveValue("old@x.com"); // 打开即预填
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "new@y.com" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "pw-123" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" })); // 不勾选
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(localStorage.getItem("fieldwork.remembered_email")).toBeNull();
  });

  it("重开弹层（login 模式）：预填已记住的邮箱，密码恒为空", () => {
    localStorage.setItem("fieldwork.remembered_email", "keep@x.com");
    const { rerender } = render(<AuthDialog open={false} onClose={() => {}} />);
    rerender(<AuthDialog open onClose={() => {}} />);
    expect(screen.getByLabelText("邮箱")).toHaveValue("keep@x.com");
    expect(screen.getByLabelText("密码")).toHaveValue("");
  });

  it("提交中按钮显示「登录中…」并禁用；busy 中不重复提交", async () => {
    let resolveLogin!: () => void;
    const login = vi.fn(() => new Promise<void>((r) => { resolveLogin = r; }));
    useAuthStore.setState({ login });
    const onClose = vi.fn();
    render(<AuthDialog open onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "a@b.com" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "pw-123" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    const busyBtn = screen.getByRole("button", { name: "登录中…" });
    expect(busyBtn).toBeDisabled();
    fireEvent.click(busyBtn); // busy 中再点：submit 的 busy 守卫应拦下
    resolveLogin();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(login).toHaveBeenCalledTimes(1);
  });

  it("注册模式提交中显示「注册中…」", () => {
    useAuthStore.setState({ register: vi.fn(() => new Promise<void>(() => {})) }); // 挂起请求
    render(<AuthDialog open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /没有账号/ }));
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "a@b.com" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "pw-123" } });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    expect(screen.getByRole("button", { name: "注册中…" })).toBeDisabled();
  });

  it("Enter 提交：form onSubmit 触发登录；唯一可隐式提交的是主钮", () => {
    const login = vi.fn(async () => {});
    useAuthStore.setState({ login });
    const { container } = render(<AuthDialog open onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "a@b.com" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "pw-123" } });
    fireEvent.submit(container.querySelector("form")!);
    expect(login).toHaveBeenCalledWith("a@b.com", "pw-123");
    // 显隐钮 / 切换模式钮 / 折叠条均 type="button"：回车误触面收窄到主钮一个
    expect(container.querySelectorAll("button:not([type='button'])")).toHaveLength(1);
  });

  it("高级折叠区默认收起不挂载 ApiKeySection，展开后挂载", async () => {
    render(<AuthDialog open onClose={() => {}} />);
    const toggle = screen.getByRole("button", { name: /高级 · API 密钥/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText(/百炼 API Key/)).toBeNull();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByLabelText(/百炼 API Key/)).toBeTruthy();
  });

  it("initialKeysOpen：打开弹层即展开高级密钥折叠区（401 分流自动展开）", async () => {
    render(<AuthDialog open initialKeysOpen onClose={() => {}} />);
    expect(screen.getByRole("button", { name: /高级 · API 密钥/ })).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByLabelText(/百炼 API Key/)).toBeTruthy();
  });
});
