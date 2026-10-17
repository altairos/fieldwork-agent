/**
 * 导出格式偏好——纯函数层（与 theme.ts 同构）。
 * localStorage 键 fieldwork.export_format；读取失败或值非法一律回落 "md"。
 * 日后接入登录/账号体系后，应随其余个人偏好一并纳入服务端持久化范畴。
 */
export type ExportFormat = "md" | "docx" | "txt";

export const EXPORT_FORMAT_STORAGE_KEY = "fieldwork.export_format";

export interface ExportFormatMeta {
  id: ExportFormat;
  label: string;
  note: string;
}

export const EXPORT_FORMATS: ExportFormatMeta[] = [
  { id: "md", label: "Markdown（.md）", note: "默认 · 保留 markdown 源格式" },
  { id: "docx", label: "Word 文档（.docx）", note: "Word/WPS 直接编辑" },
  { id: "txt", label: "纯文本（.txt）", note: "无格式纯文本，任何编辑器可读" },
];

export function isExportFormat(v: unknown): v is ExportFormat {
  return v === "md" || v === "docx" || v === "txt";
}

export function readStoredFormat(): ExportFormat {
  try {
    const v = localStorage.getItem(EXPORT_FORMAT_STORAGE_KEY);
    return isExportFormat(v) ? v : "md";
  } catch {
    return "md";
  }
}
