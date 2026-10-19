import { useCallback, useEffect, useRef, useState } from "react";
import { rest, ApiError, isAppTokenError, apiDetail } from "../api/rest";
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
export function useTurn(projectId: string, opts?: { onAuthError?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [keyMissing, setKeyMissing] = useState(false);
  const [lastTurnId, setLastTurnId] = useState<string | null>(null);
  const [retryable, setRetryable] = useState(false);
  const [incomplete, setIncomplete] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const turnIdRef = useRef<string | null>(null);
  const seenRef = useRef<Set<number>>(new Set());
  // 同步在飞守卫（评审 R4）：`busy` 是 state，`setBusy(true)` 要等本轮渲染后才为真，
  // 而它原先是在 `await createTurn` **之后**才置位——POST 落地前 busy 仍是 false，
  // 连点/回车双发会各自创建回合（后端 409 才算兜住）。当前 UI 靠「提交即清空 textarea」
  // 掩盖了这个竞态，一旦有别的触发源（快捷键、快捷键连击、程序化 send）就会暴露。
  // ref 在同一次事件回调里同步生效，与 busy（渲染态/按钮禁用）职责分离。
  const inFlightRef = useRef(false);
  // 守卫 401 回调经 ref 中转：send 不因此重造（依赖数组不抖），调用方换弹层状态不重启回合句柄。
  // 无依赖 effect 每次 render 同步最新引用（opts 可能是内联对象）。
  const onAuthErrorRef = useRef(opts?.onAuthError);
  useEffect(() => { onAuthErrorRef.current = opts?.onAuthError; });

  // 卸载 / projectId 变化 → abort 旧流（cleanup 先于新 effect 执行，符合"换项目先断旧流"语义）
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, [projectId]);

  const send = useCallback(async (text: string) => {
    if (!text.trim() || inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      setConflict(false);
      setKeyMissing(false);
      useConversationStore.getState().pushUser(text);
      useConversationStore.getState().beginTurn();

      // ── 第一步：POST 创建回合 ──
      // 失败 → failTurn 回滚乐观 UI：移除刚 push 的用户消息 + turnRunning=false + 清 buffer，
      // 否则打字光标永久脉冲且对话流留下后端未接受的消息。
      // 409（已有回合在执行）与 403（llm_key_required，服务端无 key 且请求头未带）
      // 各自进专属提示态；守卫 401（App Token）给人话横幅不透传后端 JSON，
      // 并回调调用方就地弹密钥层（App 注入 openKeyDialog）；其余如实上屏。
      let turnId: string;
      try {
        turnId = (await rest.createTurn(projectId, text)).turn_id;
      } catch (err) {
        setBusy(false);
        useConversationStore.getState().failTurn();
        if (err instanceof ApiError && err.status === 409) setConflict(true);
        else if (err instanceof ApiError && err.status === 403) setKeyMissing(true);
        else if (isAppTokenError(err)) {
          useUiStore.getState().applyEvent({
            event: "error",
            code: "send_failed",
            message: "访问令牌缺失或已失效，发送被拒绝",
            hint: "请在「密钥与登录」弹层填写访问令牌并保存",
          });
          onAuthErrorRef.current?.();
        }
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
          const snap = await rest.getProject(projectId);
          if (useProjectStore.getState().projectId !== projectId) return; // 竞态守卫：已切换项目，弃置旧快照
          useProjectStore.getState().loadSnapshot(snap);
          useProjectStore.getState().setRefreshError(null);
        } catch {
          useProjectStore.getState().setRefreshError("快照刷新失败，最新田野点/行程可能未显示");
        }
      };

      turnIdRef.current = turnId;
      setLastTurnId(turnId);
      useConversationStore.getState().setCurrentTurn(turnId);   // 证据 tab「本轮」标签依据（P1-1）
      setRetryable(false);
      setIncomplete(false);
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
            // 「重新生成」只在失败/卡住时给出（用户反馈）：正常完成的回合不给重试入口。
            // turn_done.incomplete=true → 正文未收敛/截断；error → 回合失败终态
            // （后端失败不发 turn_done，只发 error 后关流）。
            if (env.event === "turn_done") {
              if (seenRef.current !== prev) void refreshSnapshot();
              setRetryable(env.data.incomplete === true);
              setIncomplete(env.data.incomplete === true);
            } else if (env.event === "error") {
              setRetryable(true);
            }
          },
          onError: () => {
            useUiStore.getState().applyEvent({ event: "error", code: "stream_error", message: "事件流中断", hint: "请重试" });
            // 断线重连耗尽（卡住）：留重试入口；若后端仍在跑，retry 会 409 并如实提示
            setRetryable(true);
          },
          onClose: () => { setBusy(false); },
        });
      } catch (err) {
        useUiStore.getState().applyEvent({ event: "error", code: "send_failed", message: String(err), hint: "" });
      } finally {
        setBusy(false);
      }
    } finally {
      inFlightRef.current = false;      // 成功/失败/提前 return 一律释放
    }
  }, [projectId]);

  // 领养回合（首页两步创建→跳转接住，轮 4）：不 POST 创建，只订阅该回合事件流。
  // 回放的 message_delta/tool/turn_done 正常进 store；结束后 re-hydrate 对齐后端
  // 事实史——回放不含 user 帧（user 消息只落 chat_history），首页首问由覆盖式
  // hydrate 补齐（与回放的并发竞态由 conversation store 的 turn_done 查重收敛）。
  const adopt = useCallback(async (turnId: string) => {
    abortRef.current?.abort();               // StrictMode/重复领养：先断旧流
    turnIdRef.current = turnId;
    setLastTurnId(turnId);
    useConversationStore.getState().setCurrentTurn(turnId);   // 领养回合同样算「本轮」（P1-1）
    setRetryable(false);
    setIncomplete(false);
    seenRef.current = new Set();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy(true);
    try {
      await streamTurnEvents(projectId, turnId, {
        signal: ctrl.signal,
        onEvent: (env: EventEnvelope) => {
          seenRef.current = dispatchEvent(env, seenRef.current);
          // 与 send 同口径：重试入口只在 incomplete / error（失败终态）时给出
          if (env.event === "turn_done") {
            setRetryable(env.data.incomplete === true);
            setIncomplete(env.data.incomplete === true);
          } else if (env.event === "error") {
            setRetryable(true);
          }
        },
        onError: () => {
          useUiStore.getState().applyEvent({ event: "error", code: "stream_error", message: "事件流中断", hint: "请重试" });
          setRetryable(true); // 断线重连耗尽（卡住）：留重试入口
        },
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
    // P2-9：abort 断流收不到终态事件，须主动复位流式态（残缺回复丢弃，user 消息保留）
    useConversationStore.getState().cancelTurn();
    setBusy(false);
    setRetryable(false);
    setLastTurnId(null);
    setIncomplete(false);
  }, [projectId]);

  // 重新生成：只对「本会话刚结束的回合」开放（lastTurnId 由 send/adopt 写入）；
  // 必须先等 POST 成功再丢本地 assistant——失败时历史保持原样。
  const retry = useCallback(async (turnId?: string) => {
    const target = turnId ?? lastTurnId;
    if (!target || inFlightRef.current) return;
    inFlightRef.current = true;
    setKeyMissing(false);
    setRetryable(false);
    try {
      const created = await rest.retryTurn(projectId, target);
      useConversationStore.getState().dropLastAssistant();
      useConversationStore.getState().beginTurn();
      setIncomplete(false);
      await adopt(created.turn_id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setKeyMissing(true);
      else if (isAppTokenError(err)) {
        useUiStore.getState().applyEvent({
          event: "error", code: "send_failed",
          message: "访问令牌缺失或已失效，重新生成被拒绝",
          hint: "请在「密钥与登录」弹层填写访问令牌并保存",
        });
        onAuthErrorRef.current?.();
      } else {
        const detail = err instanceof ApiError ? apiDetail(err) : null;
        useUiStore.getState().applyEvent({
          event: "error", code: "retry_failed",
          message: detail ?? (err instanceof ApiError && err.status === 409
            ? "无法重新生成：对话末尾不是「用户消息 + 助手回复」"
            : String(err)),
          hint: "请等当前回合结束，或直接重新发送这条问题",
        });
      }
    } finally {
      inFlightRef.current = false;
    }
  }, [projectId, lastTurnId, adopt]);

  return {
    send, adopt, cancel, retry, busy, conflict, keyMissing,
    canRetry: retryable && lastTurnId !== null && !busy,
    incomplete,
  };
}
