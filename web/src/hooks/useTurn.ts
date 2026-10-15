import { useCallback, useEffect, useRef, useState } from "react";
import { rest, ApiError } from "../api/rest";
import { streamTurnEvents } from "../api/sse";
import { dispatchEvent } from "../lib/dispatch";
import { useConversationStore } from "../stores/conversation";
import { useUiStore } from "../stores/ui";
import type { EventEnvelope } from "../types/events";

/**
 * 回合句柄（R4 两步式：POST /messages 创建回合 → GET SSE 订阅事件流）。
 *
 * 生命周期：组件卸载或 projectId 变化时中止进行中的流——否则旧项目的 SSE 回调会继续把事件
 * 写进（已被跨项目 reset 清空的）全局 store，造成状态串台，并泄漏一条常驻连接。
 */
export function useTurn(projectId: string) {
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const turnIdRef = useRef<string | null>(null);
  const seenRef = useRef<Set<number>>(new Set());

  // 卸载 / projectId 变化 → abort 旧流（cleanup 先于新 effect 执行，符合"换项目先断旧流"语义）
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, [projectId]);

  const send = useCallback(async (text: string) => {
    if (!text.trim() || busy) return;
    setConflict(false);
    useConversationStore.getState().pushUser(text);
    useConversationStore.getState().beginTurn();

    // ── 第一步：POST 创建回合 ──
    // 失败（含 409 已有回合在执行）→ failTurn 回滚乐观 UI：移除刚 push 的用户消息 +
    // turnRunning=false + 清 buffer，否则打字光标永久脉冲且对话流留下后端未接受的消息。
    let turnId: string;
    try {
      turnId = (await rest.createTurn(projectId, text)).turn_id;
    } catch (err) {
      setBusy(false);
      useConversationStore.getState().failTurn();
      if (err instanceof ApiError && err.status === 409) setConflict(true);
      else useUiStore.getState().applyEvent({ event: "error", code: "send_failed", message: String(err), hint: "" });
      return;
    }

    // ── 第二步：订阅 SSE 事件流 ──
    // 此阶段消息已被后端接受，异常只如实上屏；绝不调 failTurn（会弹掉真实对话内容）。
    // 流内错误由 onError 回调处理（重连耗尽后），turnRunning 由 error/turn_done 事件与 onClose 负责。
    turnIdRef.current = turnId;
    seenRef.current = new Set();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy(true);
    try {
      await streamTurnEvents(projectId, turnId, {
        signal: ctrl.signal,
        onEvent: (env: EventEnvelope) => { seenRef.current = dispatchEvent(env, seenRef.current); },
        onError: () => { useUiStore.getState().applyEvent({ event: "error", code: "stream_error", message: "事件流中断", hint: "请重试" }); },
        onClose: () => { setBusy(false); },
      });
    } catch (err) {
      useUiStore.getState().applyEvent({ event: "error", code: "send_failed", message: String(err), hint: "" });
    } finally {
      setBusy(false);
    }
  }, [projectId, busy]);

  const cancel = useCallback(async () => {
    abortRef.current?.abort();
    if (turnIdRef.current) await rest.cancelTurn(projectId, turnIdRef.current).catch(() => {});
    setBusy(false);
  }, [projectId]);

  return { send, cancel, busy, conflict };
}
