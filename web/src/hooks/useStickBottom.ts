import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

/**
 * useStickBottom — 滚动容器「贴底」行为（中栏「对话 / 检索结果」两 tab 共用）。
 *
 * 背景（用户反馈）：中栏两个 tab 是懒挂载的（未选中即卸载，见 App.tsx 的三元渲染），
 * 切回来时 scrollTop 归 0 —— 每次都要从最早的一条消息往下翻，与聊天流的阅读习惯相反。
 * 现在：**挂载即贴底**；只要用户本来就停在底部，新内容（流式增量、新卡片、新证据行）
 * 继续跟随；用户手动上滚阅读时暂停跟随，滚回底部后自动恢复。
 *
 * 为什么用 MutationObserver 而不是把 content 当依赖传进来：Timeline 刻意不订阅
 * streamBuffer（每 token 变一次会让整条时间线重解析全部历史 markdown，见 Timeline 顶部
 * 注释）。用 DOM 变更观测把「要不要跟到底」这件事留在 DOM 层，React 渲染次数零增加。
 *
 * 判定阈值 24px 而非精确相等：行高亚像素与滚动条取整会让 scrollTop 差 1–2px，
 * 用相等判定会在每次程序滚动后被误判成「用户上滚」从而永久停止跟随。
 *
 * P1-6（外部可控）：自动贴底不能把别人的 `scrollTop` 覆盖掉。浏览器把 scroll 事件
 * 派发到下一帧，而 MutationObserver 回调常常先到——旧实现只看 `stick.current`，
 * 于是 `el.scrollTop = N` 刚写下就被贴底改回底部（录制/自动化无法把历史消息滚进画面）。
 * 现在记录「上次贴底后的位置」`lastAuto`：下一帧发现位置被人改过就判定外部接管，
 * 立即停止跟随；`scrollToTop`/`scrollToMessage` 则显式接管并同样停止自动贴底。
 */

/** 视为「仍在底部」的容差（px）。 */
const STICK_TOLERANCE = 24;

/** requestAnimationFrame 在 jsdom/SSR 下可能缺席，缺失时退化为同步执行。 */
function nextFrame(fn: () => void): () => void {
  if (typeof requestAnimationFrame !== "function") {
    fn();
    return () => {};
  }
  const id = requestAnimationFrame(fn);
  return () => cancelAnimationFrame(id);
}

export function useStickBottom<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const stick = useRef(true);
  // 排程中的续帧取消器（卸载/重排程时撤销，避免对已卸载节点写 scrollTop）
  const cancel = useRef<() => void>(() => {});
  // 上次由**本 hook** 写入的 scrollTop；用于识别“别人动过滚动条”
  const lastAuto = useRef<number | null>(null);

  const snapToBottom = useCallback((el: T) => {
    el.scrollTop = el.scrollHeight;
    lastAuto.current = el.scrollTop;
  }, []);

  // 挂载即贴底（layout 阶段：首帧就落在底部，不出现"先看到最早一条再跳"）
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    stick.current = true;
    snapToBottom(el);
    // 字体度量/图片落位会改变 scrollHeight，下一帧再校正一次
    cancel.current = nextFrame(() => {
      if (stick.current) snapToBottom(el);
    });
    return () => cancel.current();
  }, [snapToBottom]);

  // 内容变化跟随（同一帧内多次变更合并为一次滚动）
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof MutationObserver !== "function") return;
    let pending = false;
    const follow = () => {
      if (pending || !stick.current) return;
      pending = true;
      cancel.current = nextFrame(() => {
        pending = false;
        if (!stick.current) return;
        // 外部接管检测：位置既不是我们上次写下的，也远离底部 → 有人在控制滚动条
        if (lastAuto.current !== null
            && Math.abs(el.scrollTop - lastAuto.current) > STICK_TOLERANCE) {
          stick.current = false;
          return;
        }
        snapToBottom(el);
      });
    };
    const mo = new MutationObserver(follow);
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    return () => {
      mo.disconnect();
      cancel.current();
    };
  }, [snapToBottom]);

  /** 挂到容器的 onScroll：仅在用户把容器滚回底部时恢复跟随。 */
  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_TOLERANCE;
    stick.current = atBottom;
    if (atBottom) lastAuto.current = el.scrollTop;
  }, []);

  /** 外部接管：滚到指定位置（同时停止自动贴底，保证写入的值不会被下一帧改回）。 */
  const scrollToTop = useCallback((top: number) => {
    const el = ref.current;
    if (!el) return false;
    stick.current = false;
    el.scrollTop = top;
    lastAuto.current = el.scrollTop;
    return true;
  }, []);

  /** 外部接管：把某条消息滚到容器顶部。target 可为 `id` 或 `data-msg-index`。 */
  const scrollToMessage = useCallback((target: string | number) => {
    const el = ref.current;
    if (!el) return false;
    const selector = typeof target === "number"
      ? `[data-msg-index="${target}"]`
      : `#${target}`;
    const node = el.querySelector<HTMLElement>(selector);
    if (!node) return false;
    const top = el.scrollTop + (node.getBoundingClientRect().top
      - el.getBoundingClientRect().top);
    return scrollToTop(top);
  }, [scrollToTop]);

  return { ref, onScroll, scrollToTop, scrollToMessage };
}
