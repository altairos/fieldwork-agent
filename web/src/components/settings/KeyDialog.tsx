import { useEffect, useRef } from "react";
import { ApiKeySection } from "./ApiKeyCard";
import { IconLock } from "../ui/icons";

/**
 * KeyDialog — 首页「密钥 / 登录」弹层：只承载 ApiKeySection（百炼 Key / 访问令牌 / 高级选项）。
 * 首页极简重设计：导出格式与配色偏好不放弹层（导出格式在提案页下载区、配色走对话页 Header）。
 * 仅 open 时挂载（关闭即卸载，ApiKeySection 草稿态随之丢弃）；关闭三通道：
 * 右上「关闭」钮 / Escape / 点击遮罩；打开时焦点落在「关闭」钮。
 * 动效用 tokens.css 全局 keyframes（fade-in 遮罩 / sheet-in 面板），reduced-motion 自动禁用。
 */
export function KeyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  if (!open) return null;
  return (
    <div
      role="presentation"
      onClick={onClose}
      className="fixed inset-0 z-50 grid place-items-center px-4 py-8"
    >
      <div className="absolute inset-0 bg-ink/40 fade-in fw-scrim" aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="密钥与登录"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
        className="relative hairline rounded-xl fw-glass g1 w-full max-w-[540px] max-h-full overflow-y-auto scroll sheet-in"
      >
        <div className="sticky top-0 bg-card flex items-center justify-between gap-3 px-5 h-14 border-b border-hairline">
          <div className="flex items-center gap-2 font-serif text-[15px] font-semibold">
            <IconLock className="w-4 h-4 text-cinnabar" />
            密钥与登录
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="h-7 px-3 rounded-md hairline bg-card text-[12px] text-inksoft hover:bg-cardsunk transition cursor-pointer shrink-0"
          >
            关闭
          </button>
        </div>
        <div className="p-5">
          <ApiKeySection />
        </div>
      </div>
    </div>
  );
}
