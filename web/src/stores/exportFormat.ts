/**
 * 导出格式偏好 store（照 theme.ts：zustand create + localStorage 写入 try/catch）。
 */
import { create } from "zustand";
import {
  EXPORT_FORMAT_STORAGE_KEY,
  readStoredFormat,
  type ExportFormat,
} from "../lib/exportFormat";

interface ExportFormatState {
  format: ExportFormat;
  setFormat: (f: ExportFormat) => void;
}

export const useExportFormatStore = create<ExportFormatState>()((set) => ({
  format: readStoredFormat(),
  setFormat: (f) => {
    try {
      localStorage.setItem(EXPORT_FORMAT_STORAGE_KEY, f);
    } catch {}
    set({ format: f });
  },
}));
