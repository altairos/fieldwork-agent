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

// ── Task 6 追加：工具卡片所需图标（同一 symbol 库逐条提取，禁 emoji 红线）──

/** 折叠箭头（details 展开态由 tokens.css `.caret` 旋转 90°） · line 135 #ic-caret（1.8px 描边） */
export function IconCaret(props: IconProps) {
  return <Line strokeWidth={1.8} {...props}><path d="m9 6 6 6-6 6" /></Line>;
}

/** 航班（大交通） · line 123 #ic-plane */
export function IconPlane(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M10.5 13.5 4 11l1.5-1.5 5 .5 3-3.5c.7-.8 2.3-1 3-.3s.5 2.3-.3 3L13 12.5l.5 5L12 19z" />
    </Line>
  );
}

/** 火车/高铁（大交通备选） · line 124 #ic-train */
export function IconTrain(props: IconProps) {
  return (
    <Line {...props}>
      <rect x="5" y="3" width="14" height="13" rx="3" />
      <path d="M5 10h14M8 20l2-3M16 20l-2-3" />
      <circle cx="9" cy="13" r=".6" />
      <circle cx="15" cy="13" r=".6" />
    </Line>
  );
}

/** 床（住宿） · line 125 #ic-bed */
export function IconBed(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M3 18v-8m0 3h18v5M3 12h7a3 3 0 0 1 3 3v0" />
      <path d="M21 18v-3a3 3 0 0 0-3-3h-4" />
      <circle cx="7" cy="10" r="1.6" />
    </Line>
  );
}

/** 庙宇（文化资源/兴趣点） · line 126 #ic-temple */
export function IconTemple(props: IconProps) {
  return <Line {...props}><path d="M4 9 12 4l8 5M6 9v10m12-10v10M4 19h16M10 19v-5h4v5" /></Line>;
}

/** 钱包（预算） · line 127 #ic-wallet */
export function IconWallet(props: IconProps) {
  return (
    <Line {...props}>
      <rect x="3" y="6" width="18" height="13" rx="2.5" />
      <path d="M3 10h18M16.5 14.5h1.5" />
    </Line>
  );
}

/** 人群（访谈对象） · line 128 #ic-users */
export function IconUsers(props: IconProps) {
  return (
    <Line {...props}>
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 19a5.5 5.5 0 0 1 11 0M16 6.2a3 3 0 0 1 0 5.6M17.5 19a5.4 5.4 0 0 0-2-4" />
    </Line>
  );
}

/** 书本（提案/访谈提纲文档） · line 129 #ic-book */
export function IconBook(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5z" />
      <path d="M4 5.5v15" />
    </Line>
  );
}

// ── Task 8 追加：ProjectHub 项目卡所需（同一 symbol 库提取）──

/** 时钟（更新时间） · line 138 #ic-clock */
export function IconClock(props: IconProps) {
  return (
    <Line {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </Line>
  );
}

// ── 主题切换（ThemeSwitcher）追加 ──

/** 调色板（界面配色） · 无设计板 symbol，按同一线形语言补画 */
export function IconPalette(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M12 3a9 9 0 1 0 0 18c.8 0 1.5-.7 1.5-1.5 0-.4-.15-.75-.4-1-.25-.3-.4-.65-.4-1 0-.8.7-1.5 1.5-1.5H16a5 5 0 0 0 5-5c0-4.4-4-8-9-8Z" />
      <circle cx="7.5" cy="11.5" r="1" />
      <circle cx="10.5" cy="7.5" r="1" />
      <circle cx="15" cy="8.5" r="1" />
    </Line>
  );
}

// ── 移动端适配（批次 A）追加 ──

/** 菜单（Header 汉堡钮，触发左栏抽屉） · 无设计板 symbol，按同一线形语言补画 */
export function IconMenu(props: IconProps) {
  return <Line {...props}><path d="M4 6h16M4 12h16M4 18h16" /></Line>;
}

// ── 「我的」卡（导出格式偏好）追加 ──

/** 单人（「我的」卡） · 无设计板 symbol，按同一线形语言补画 */
export function IconUser(props: IconProps) {
  return (
    <Line {...props}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </Line>
  );
}

// ── 项目删除（行内二次确认）追加 ──

/** 垃圾桶（项目删除） · 无设计板 symbol，按同一线形语言补画 */
export function IconTrash(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M4 7h16M9.5 7V5A1.5 1.5 0 0 1 11 3.5h2A1.5 1.5 0 0 1 14.5 5v2M6.5 7l.8 12a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9l.8-12" />
      <path d="M10 11v6M14 11v6" />
    </Line>
  );
}
