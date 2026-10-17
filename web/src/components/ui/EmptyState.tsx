import type { ReactNode } from "react";

/**
 * EmptyState — 空状态占位。
 * 设计板同源：view-hub 新建空项目卡（lines 360-364）：
 *   圆形图标位 `w-9 h-9 grid place-items-center rounded-full hairline text-inkfaint`
 *   标题 `text-[13px] text-inksoft`；提示 `text-[11px] text-inkfaint mt-0.5`；整体居中。
 * icon 传入线形 SVG 元素（设计红线③：禁 emoji）。
 * children：可选操作区（如示例 chips），渲染在提示下方 mt-3，由调用方决定内容与交互。
 */
export function EmptyState({
  icon,
  title,
  hint,
  className = "",
  children,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`flex flex-col items-center justify-center text-center py-10 ${className}`.trim()}>
      {icon && (
        <span className="w-9 h-9 grid place-items-center rounded-full hairline text-inkfaint mb-2">
          {icon}
        </span>
      )}
      <div className="text-[13px] text-inksoft">{title}</div>
      {hint && <div className="text-[11px] text-inkfaint mt-0.5">{hint}</div>}
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}
