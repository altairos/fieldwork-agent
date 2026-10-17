import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { ExportFormatChips } from "../src/components/proposal/ExportFormatChips";
import { EXPORT_FORMAT_STORAGE_KEY } from "../src/lib/exportFormat";
import { useExportFormatStore } from "../src/stores/exportFormat";

// 「我的」卡解体（首页极简重设计）：导出格式 chips 迁至提案页下载区，
// 断言语义自 myCard.spec.tsx 迁移——aria-pressed 三态 + store/localStorage 同步。
describe("ExportFormatChips（提案页导出格式选择）", () => {
  beforeEach(() => {
    localStorage.clear();
    useExportFormatStore.setState({ format: "md" });
  });
  afterEach(cleanup);

  test("三 chips：默认选中 md，其余未选中", () => {
    render(<ExportFormatChips />);
    const md = screen.getByRole("button", { name: "Markdown（.md）" });
    const docx = screen.getByRole("button", { name: "Word 文档（.docx）" });
    const txt = screen.getByRole("button", { name: "纯文本（.txt）" });
    expect(md).toHaveAttribute("aria-pressed", "true");
    expect(docx).toHaveAttribute("aria-pressed", "false");
    expect(txt).toHaveAttribute("aria-pressed", "false");
  });

  test("点击 docx：store 与 localStorage 同步更新，选中态切换", () => {
    render(<ExportFormatChips />);
    const docx = screen.getByRole("button", { name: "Word 文档（.docx）" });
    fireEvent.click(docx);
    expect(useExportFormatStore.getState().format).toBe("docx");
    expect(localStorage.getItem(EXPORT_FORMAT_STORAGE_KEY)).toBe("docx");
    expect(docx).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Markdown（.md）" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  test("跟随 store 偏好：docx 时 docx chip 选中", () => {
    useExportFormatStore.setState({ format: "docx" });
    render(<ExportFormatChips />);
    expect(screen.getByRole("button", { name: "Word 文档（.docx）" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
