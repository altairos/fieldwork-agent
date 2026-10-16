import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import * as rest from "../src/api/rest";
import { MyCard } from "../src/components/settings/MyCard";
import { EXPORT_FORMAT_STORAGE_KEY } from "../src/lib/exportFormat";
import { useExportFormatStore } from "../src/stores/exportFormat";
import { useThemeStore } from "../src/stores/theme";

vi.mock("../src/api/rest");

describe("MyCard（「我的」卡）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useExportFormatStore.setState({ format: "md" });
    useThemeStore.setState({ theme: "cinnabar" });
    vi.mocked(rest.rest.settingsStatus).mockResolvedValue({
      dashscope_configured: true,
      flyai_configured: true,
    });
    vi.mocked(rest.rest.health).mockResolvedValue({
      status: "ok",
      auth_required: false,
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("三节俱在：导出格式 chips + 配色入口 + 密钥节", async () => {
    render(<MyCard />);
    expect(screen.getByText("导出格式")).toBeDefined();
    expect(
      screen.getByRole("button", { name: "切换界面配色" })
    ).toBeDefined();
    expect(await screen.findByPlaceholderText("sk-…")).toBeDefined();
  });

  test("导出格式三 chips：默认选中 md，其余未选中", () => {
    render(<MyCard />);
    const md = screen.getByRole("button", { name: "Markdown（.md）" });
    const docx = screen.getByRole("button", { name: "Word 文档（.docx）" });
    const txt = screen.getByRole("button", { name: "纯文本（.txt）" });
    expect(md).toHaveAttribute("aria-pressed", "true");
    expect(docx).toHaveAttribute("aria-pressed", "false");
    expect(txt).toHaveAttribute("aria-pressed", "false");
  });

  test("点击 docx chip：store 与 localStorage 同步更新，选中态反转", () => {
    render(<MyCard />);
    const docx = screen.getByRole("button", { name: "Word 文档（.docx）" });
    fireEvent.click(docx);
    expect(useExportFormatStore.getState().format).toBe("docx");
    expect(localStorage.getItem(EXPORT_FORMAT_STORAGE_KEY)).toBe("docx");
    expect(docx).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Markdown（.md）" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
  });
});
