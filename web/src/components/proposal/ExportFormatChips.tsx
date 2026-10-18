import { EXPORT_FORMATS } from "../../lib/exportFormat";
import { useExportFormatStore } from "../../stores/exportFormat";

/**
 * ExportFormatChips — 提案页下载区的导出格式选择（「我的」卡解体后自首页迁此，
 * 样式与选中态沿用 MyCard 原导出节：单选朱砂 wash、aria-pressed）。
 * 点击写 zustand store（内部同步持久化 localStorage）；ProposalView 订阅
 * format 决定 by-id 下载 URL 与「下载 .{format}」按钮文案。
 * 布局（2026-10-18）：标签在左、chips 成组右对齐（justify-between 需两子元素，
 * chips 必须包一层组容器，否则 4 个子元素被均匀分散）。
 */
export function ExportFormatChips() {
  const format = useExportFormatStore((s) => s.format);
  const setFormat = useExportFormatStore((s) => s.setFormat);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-[11px] text-inkfaint shrink-0">导出格式</span>
      <div className="flex flex-wrap items-center gap-2">
        {EXPORT_FORMATS.map((f) => (
          <button
            key={f.id}
            type="button"
            aria-pressed={format === f.id}
            onClick={() => setFormat(f.id)}
            title={f.note}
            className={`chip transition cursor-pointer ${
              format === f.id
                ? "bg-cinnabarwash text-cinnabar border-cinnabar/25"
                : "bg-card hairline text-inksoft hover:bg-cardsunk"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
    </div>
  );
}
