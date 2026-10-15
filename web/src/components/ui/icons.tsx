import type { ReactNode, SVGProps } from "react";

/**
 * 线形图标库 — 与设计板同源（design-board.html lines 117-138 symbol 定义）。
 * 设计红线③：界面禁 emoji，图标一律线形 SVG（stroke currentColor，1.5px 描边）。
 * 默认 20px（w-5 h-5 由调用处 className 控制），viewBox 24。
 */

type IconProps = SVGProps<SVGSVGElement> & { children?: ReactNode };

function Line({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

/** 罗盘（品牌/行程） · line 121 #ic-compass */
export function IconCompass(props: IconProps) {
  return (
    <Line {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="m15.5 8.5-2.1 5-5 2.1 2.1-5z" />
    </Line>
  );
}

/** 对勾（已完成） · line 130 #ic-check（2px 描边） */
export function IconCheck(props: IconProps) {
  return <Line strokeWidth={2} {...props}><path d="m5 13 4 4L19 7" /></Line>;
}

/** 锁（未解锁） · line 131 #ic-lock */
export function IconLock(props: IconProps) {
  return (
    <Line {...props}>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </Line>
  );
}

/** 星芒（切换建议/智能提示） · line 133 #ic-spark */
export function IconSpark(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.8 2.8M14.9 14.9l2.8 2.8M17.7 6.3l-2.8 2.8M9.1 14.9l-2.8 2.8" />
    </Line>
  );
}

/** 下载（导出） · line 134 #ic-download */
export function IconDownload(props: IconProps) {
  return <Line {...props}><path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14" /></Line>;
}

/** 发送 · line 136 #ic-send */
export function IconSend(props: IconProps) {
  return <Line {...props}><path d="M4 12 20 5l-7 15-2-6z" /></Line>;
}

/** 加号（新建/添加） · line 137 #ic-plus（1.6px 描边） */
export function IconPlus(props: IconProps) {
  return <Line strokeWidth={1.6} {...props}><path d="M12 5v14M5 12h14" /></Line>;
}

/** 定位针（田野点） · line 122 #ic-pin */
export function IconPin(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11z" />
      <circle cx="12" cy="10" r="2.5" />
    </Line>
  );
}

/** 警告三角（降级/错误） · line 132 #ic-alert */
export function IconAlert(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M12 4 2.8 20h18.4z" />
      <path d="M12 10v4M12 17.2v.2" />
    </Line>
  );
}
