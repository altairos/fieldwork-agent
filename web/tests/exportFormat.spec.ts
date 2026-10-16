import { beforeEach, describe, expect, test } from "vitest";
import {
  EXPORT_FORMATS,
  EXPORT_FORMAT_STORAGE_KEY,
  isExportFormat,
  readStoredFormat,
} from "../src/lib/exportFormat";
import { useExportFormatStore } from "../src/stores/exportFormat";

describe("lib/exportFormat", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  test("isExportFormat：三真两假", () => {
    expect(isExportFormat("md")).toBe(true);
    expect(isExportFormat("docx")).toBe(true);
    expect(isExportFormat("txt")).toBe(true);
    expect(isExportFormat("pdf")).toBe(false);
    expect(isExportFormat(null)).toBe(false);
  });

  test("readStoredFormat：无值回落 md", () => {
    expect(readStoredFormat()).toBe("md");
  });

  test("readStoredFormat：非法值回落 md", () => {
    localStorage.setItem(EXPORT_FORMAT_STORAGE_KEY, "rtf");
    expect(readStoredFormat()).toBe("md");
  });

  test("readStoredFormat：合法值原样返回", () => {
    localStorage.setItem(EXPORT_FORMAT_STORAGE_KEY, "docx");
    expect(readStoredFormat()).toBe("docx");
  });

  test("EXPORT_FORMATS：顺序 md/docx/txt 且 label/note 完整", () => {
    expect(EXPORT_FORMATS.map((f) => f.id)).toEqual(["md", "docx", "txt"]);
    for (const f of EXPORT_FORMATS) {
      expect(f.label.length).toBeGreaterThan(0);
      expect(f.note.length).toBeGreaterThan(0);
    }
  });
});

describe("stores/exportFormat", () => {
  beforeEach(() => {
    localStorage.clear();
    useExportFormatStore.setState({ format: "md" });
  });

  test("初始态 md", () => {
    expect(useExportFormatStore.getState().format).toBe("md");
  });

  test("setFormat 同步 state 与 localStorage", () => {
    useExportFormatStore.getState().setFormat("docx");
    expect(useExportFormatStore.getState().format).toBe("docx");
    expect(localStorage.getItem(EXPORT_FORMAT_STORAGE_KEY)).toBe("docx");
  });
});
