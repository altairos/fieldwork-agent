// web/tests/authDialog.spec.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AuthDialog } from "../src/components/auth/AuthDialog";

describe("AuthDialog", () => {
  it("渲染邮箱/密码输入与登录按钮", () => {
    render(<AuthDialog open onClose={() => {}} />);
    expect(screen.getByLabelText(/邮箱/)).toBeTruthy();
    expect(screen.getByLabelText(/密码/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /登录/ })).toBeTruthy();
  });
});
