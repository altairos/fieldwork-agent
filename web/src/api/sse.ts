import type { EventEnvelope } from "../types/events";
import { authHeaders } from "../lib/keys";
import { ownerHeaders } from "../lib/session";

export interface ParseResult { frames: EventEnvelope[]; rest: string; }

/** 把累积的 SSE 文本切成完整帧；不完整尾帧留在 rest（处理粘包/分包）。 */
export function parseSseFrames(buffer: string): ParseResult {
  const frames: EventEnvelope[] = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";          // 最后一段可能不完整，留到下轮
  for (const block of blocks) {
    const lines = block.split("\n");
    let event = "";
    const dataLines: string[] = [];
    let id = "";
    let isCommentOnly = true;
    for (const line of lines) {
      if (line.startsWith(":")) continue;                 // 注释/心跳
      isCommentOnly = false;
      if (line.startsWith("event:")) event = line.slice(6).trim();
      // SSE 规范：data 值只去掉冒号后的**单个**前导空格（不是 trim 掉全部空白）
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      else if (line.startsWith("id:")) id = line.slice(3).trim();
    }
    if (isCommentOnly || dataLines.length === 0) continue; // 纯心跳或无 data：跳过
    const dataStr = dataLines.join("\n");                  // 多行 data 按规范以 \n 连接
    try {
      const data = JSON.parse(dataStr) as Record<string, unknown>;
      const seq = typeof data.seq === "number" ? data.seq : Number(id);
      frames.push({ turn_id: String(data.turn_id ?? ""), seq,
                    event: event as EventEnvelope["event"], data });
    } catch { /* 非法 JSON 帧丢弃 */ }
  }
  return { frames, rest };
}

export interface StreamOptions {
  lastEventId?: number;
  signal?: AbortSignal;
  onEvent: (env: EventEnvelope) => void;
  onError?: (err: Error) => void;
  onClose?: () => void;
  maxReconnects?: number;
}

/** GET 订阅回合事件流；断线按 Last-Event-ID 指数退避重连。 */
export async function streamTurnEvents(
  projectId: string, turnId: string, opts: StreamOptions
): Promise<void> {
  let lastId = opts.lastEventId ?? 0;
  let reconnects = 0;
  const maxReconnects = opts.maxReconnects ?? 5;
  const url = `/projects/${projectId}/turns/${turnId}/events`;

  while (true) {
    if (opts.signal?.aborted) return;
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream", ...authHeaders(), ...ownerHeaders() };
      if (lastId > 0) headers["Last-Event-ID"] = String(lastId);
      const resp = await fetch(url, { headers, signal: opts.signal });
      if (!resp.ok || !resp.body) throw new Error(`SSE HTTP ${resp.status}`);
      reconnects = 0;                       // 连上即重置退避
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let closed = false;
      try {
        while (!closed) {
          const { value, done } = await reader.read();
          if (done) { closed = true; break; }
          buffer += decoder.decode(value, { stream: true });
          const { frames, rest } = parseSseFrames(buffer);
          buffer = rest;
          for (const f of frames) {
            if (f.seq > lastId) lastId = f.seq;
            opts.onEvent(f);
            if (f.event === "turn_done" || f.event === "error") { closed = true; }
          }
        }
      } finally {
        // 无论 turn_done/error 提前终止、流自然结束（done）还是 read 抛出，都取消 reader 释放底层连接：
        // 后端发完 turn_done 后仍阻塞在 queue.get() 等哨兵（心跳续命），不主动关流；客户端只是停止
        // 读取会让 TCP 连接滞留，多回合后耗尽浏览器同域并发上限（HTTP/1.1 ~6）导致前端卡死。
        // abort 场景已由 signal 断开，cancel 幂等安全，其可能的拒绝一并吞掉。
        await reader.cancel().catch(() => {});
      }
      opts.onClose?.();
      return;                               // 正常结束（turn_done/error 或流关闭）
    } catch (err) {
      if (opts.signal?.aborted) return;
      if (reconnects >= maxReconnects) { opts.onError?.(err as Error); return; }
      reconnects++;
      await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** reconnects, 8000)));
    }
  }
}
