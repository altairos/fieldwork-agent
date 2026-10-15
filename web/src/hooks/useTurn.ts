import { useCallback, useRef, useState } from "react";
import { rest, ApiError } from "../api/rest";
import { streamTurnEvents } from "../api/sse";
import { dispatchEvent } from "../lib/dispatch";
import { useConversationStore } from "../stores/conversation";
import { useUiStore } from "../stores/ui";
import type { EventEnvelope } from "../types/events";

export function useTurn(projectId: string) {
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const turnIdRef = useRef<string | null>(null);
  const seenRef = useRef<Set<number>>(new Set());

  const send = useCallback(async (text: string) => {
    if (!text.trim() || busy) return;
    setConflict(false);
    useConversationStore.getState().pushUser(text);
    useConversationStore.getState().beginTurn();
    try {
      const { turn_id } = await rest.createTurn(projectId, text);
      turnIdRef.current = turn_id;
      seenRef.current = new Set();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setBusy(true);
      await streamTurnEvents(projectId, turn_id, {
        signal: ctrl.signal,
        onEvent: (env: EventEnvelope) => { seenRef.current = dispatchEvent(env, seenRef.current); },
        onError: () => { useUiStore.getState().applyEvent({ event: "error", code: "stream_error", message: "事件流中断", hint: "请重试" }); },
        onClose: () => { setBusy(false); },
      });
      setBusy(false);
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError && err.status === 409) setConflict(true);
      else useUiStore.getState().applyEvent({ event: "error", code: "send_failed", message: String(err), hint: "" });
    }
  }, [projectId, busy]);

  const cancel = useCallback(async () => {
    abortRef.current?.abort();
    if (turnIdRef.current) await rest.cancelTurn(projectId, turnIdRef.current).catch(() => {});
    setBusy(false);
  }, [projectId]);

  return { send, cancel, busy, conflict };
}
