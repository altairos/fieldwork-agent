import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * Button — 按钮。
 * 设计板同源（lines 242-244 按钮规格）：
 *   primary → bg-cinnabar text-ontint hover:bg-tintpress（line 242「生成研究方案」；
 *              原设计板 token 外用值 #F7EFE6/#96382a 已主题化为 ontint/tintpress）
 *   outline → hairline bg-card text-ink hover:bg-cardsunk（line 243「下载 .md」）
 *   ghost   → text-indigo hover:bg-indigowash（line 244「查看画像」）
 * size：sm = h-8 px-3 text-[12.5px]（line 536 文字按钮级别）；md = h-9 px-4 text-[13px]（lines 242-244）
 */
export type ButtonVariant = "primary" | "outline" | "ghost";

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-cinnabar text-ontint hover:bg-tintpress",
  outline: "hairline bg-card text-ink hover:bg-cardsunk",
  ghost: "text-indigo hover:bg-indigowash",
};

const SIZE = {
  sm: "h-8 px-3 text-[12.5px]",
  md: "h-9 px-4 text-[13px]",
} as const;

interface ButtonOwnProps {
  variant?: ButtonVariant;
  size?: keyof typeof SIZE;
  children: ReactNode;
  className?: string;
  type?: "button" | "submit" | "reset";
}

export function Button({
  variant = "primary",
  size = "md",
  children,
  className = "",
  type = "button",
  ...rest
}: ButtonOwnProps & Omit<ButtonHTMLAttributes<HTMLButtonElement>, keyof ButtonOwnProps>) {
  return (
    <button
      type={type}
      className={`rounded-md font-medium transition inline-flex items-center justify-center gap-2 ${VARIANT[variant]} ${SIZE[size]} disabled:opacity-50 disabled:pointer-events-none ${className}`.trim()}
      {...rest}
    >
      {children}
    </button>
  );
}
