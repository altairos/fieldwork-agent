import { useCallback, useEffect, useRef, useState } from "react";
import { rest, ApiError } from "../api/rest";
import { streamTurnEvents } from "../api/sse";
import { dispatchEvent } from "../lib/dispatch";
import { useConversationStore } from "../stores/conversation";
import { useProjectStore } from "../stores/project";
import { useUiStore } from "../stores/ui";
import type { EventEnvelope } from "../types/events";

/**
 * 回合句柄（R4 两步式：POST /messages 创建回合 → GET SSE 订阅事件流；
 * adopt 领养首页已创建的首轮回合，只订阅不创建）。
 *
 * 生命周期：组件卸载或 projectId 变化时中止进行中的流——否则旧项目的 SSE 回调会继续把事件
 * 写进（已被跨项目 reset 清空的）全局 store，造成状态串台，并泄漏一条常驻连接。
 */
export function useTurn(projectId: string) {
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [keyMissing, setKeyMissing] = useState(false);
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
    setKeyMissing(false);
    useConversationStore.getState().pushUser(text);
    useConversationStore.getState().beginTurn();

    // ── 第一步：POST 创建回合 ──
    // 失败 → failTurn 回滚乐观 UI：移除刚 push 的用户消息 + turnRunning=false + 清 buffer，
    // 否则打字光标永久脉冲且对话流留下后端未接受的消息。
    // 409（已有回合在执行）与 403（llm_key_required，服务端无 key 且请求头未带）
    // 各自进专属提示态，不走通用 error 事件。
    let turnId: string;
    try {
      turnId = (await rest.createTurn(projectId, text)).turn_id;
    } catch (err) {
      setBusy(false);
      useConversationStore.getState().failTurn();
      if (err instanceof ApiError && err.status === 409) setConflict(true);
      else if (err instanceof ApiError && err.status === 403) setKeyMissing(true);
      else useUiStore.getState().applyEvent({ event: "error", code: "send_failed", message: String(err), hint: "" });
      return;
    }

    // ── 第二步：订阅 SSE 事件流 ──
    // 此阶段消息已被后端接受，异常只如实上屏；绝不调 failTurn（会弹掉真实对话内容）。
    // 流内错误由 onError 回调处理（重连耗尽后），turnRunning 由 error/turn_done 事件与 onClose 负责。

    // ── R06a：回合末回读项目快照 ──
    // 工具产物（田野点画像等）轮末才落盘，流内事件不含持久化记录；首见 turn_done 后异步回读，
    // 失败只置 refreshError 提示态，绝不向回合流抛错、不阻塞收尾。
    const refreshSnapshot = async () => {
      try {
        useProjectStore.getState().loadSnapshot(await rest.getProject(projectId));
        useProjectStore.getState().setRefreshError(null);
      } catch {
        useProjectStore.getState().setRefreshError("快照刷新失败，最新田野点/行程可能未显示");
      }
    };

    turnIdRef.current = turnId;
    seenRef.current = new Set();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy(true);
    try {
      await streamTurnEvents(projectId, turnId, {
        signal: ctrl.signal,
        onEvent: (env: EventEnvelope) => {
          // dispatchEvent 返回新 Set 才是首见帧（引用语义）——重连重放不重复触发回读
          const prev = seenRef.current;
          seenRef.current = dispatchEvent(env, seenRef.current);
          if (env.event === "turn_done" && seenRef.current !== prev) void refreshSnapshot();
        },
        onError: () => { useUiStore.getState().applyEvent({ event: "error", code: "stream_error", message: "事件流中断", hint: "请重试" }); },
        onClose: () => { setBusy(false); },
      });
    } catch (err) {
      useUiStore.getState().applyEvent({ event: "error", code: "send_failed", message: String(err), hint: "" });
    } finally {
      setBusy(false);
    }
  }, [projectId, busy]);

  // 领养回合（首页两步创建→跳转接住，轮 4）：不 POST 创建，只订阅该回合事件流。
  // 回放的 message_delta/tool/turn_done 正常进 store；结束后 re-hydrate 对齐后端
  // 事实史——回放不含 user 帧（user 消息只落 chat_history），首页首问由覆盖式
  // hydrate 补齐（与回放的并发竞态由 conversation store 的 turn_done 查重收敛）。
  const adopt = useCallback(async (turnId: string) => {
    abortRef.current?.abort();               // StrictMode/重复领养：先断旧流
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
      // re-hydrate：以 GET /messages 的后端事实为准（含 user 首问）；失败静默——
      // store 已有回放内容兑底，刷新自愈
      rest.getMessages(projectId)
        .then((msgs) => useConversationStore.getState().hydrate(msgs))
        .catch(() => {});
    }
  }, [projectId]);

  const cancel = useCallback(async () => {
    abortRef.current?.abort();
    if (turnIdRef.current) await rest.cancelTurn(projectId, turnIdRef.current).catch(() => {});
    setBusy(false);
  }, [projectId]);

  return { send, adopt, cancel, busy, conflict, keyMissing };
}
