import type { EventEnvelope } from "../types/events";
import { authHeaders } from "../lib/keys";
import { getAuthEpoch, getSessionToken, isStaleSession, ownerHeaders } from "../lib/session";
import { ApiError, notifySessionExpiredIfAny } from "./rest";

// 帧分隔符的字符常量（评审 R3）：用字符编码构造，避免手写转义序列在编辑链中走样
const CR = String.fromCharCode(13);
const LF = String.fromCharCode(10);
const CRLF_RE = new RegExp(CR + LF + "?", "g");

export interface ParseResult { frames: EventEnvelope[]; rest: string; }

/** 把累积的 SSE 文本切成完整帧；不完整尾帧留在 rest（处理粘包/分包）。 */
export function parseSseFrames(buffer: string): ParseResult {
  const frames: EventEnvelope[] = [];
  // 兼容 CRLF（评审 R3）：帧分隔符原先只认 "\n\n"，中间层一旦把 LF 改写成 CRLF，
  // 所有帧会粘成一帧直到流结束——turn_done 迟迟不到，前端一直 busy。
  // 归一后 rest 也取自归一结果：被分包切开的半个 CRLF（孤立 "\r"）留在 rest 里，
  // 下一轮与 "\n" 重新拼成完整换行。
  const blocks = buffer.replace(CRLF_RE, LF).split(LF + LF);
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
  /** 空闲看门狗：连续多久收不到任何字节（含心跳注释帧）即判定连接已死，默认 45s；
   *  传 0 关闭（仅测试或明确接受挂死风险的场合）。 */
  idleTimeoutMs?: number;
}

/**
 * 读一个 chunk，带空闲超时（评审 R2）。
 *
 * 后端发完 turn_done 后回到 queue.get() 靠心跳续命、**不主动关流**；若中间层静默保持
 * 连接或后端卡死，`read()` 会永久挂起：`busy` 永真，用户连「重新生成」入口都看不到。
 * 超时抛错 → 落进外层 catch 的退避重连；重连次数有上限，最终 onError 让 UI 恢复可操作。
 * 超时后 pending 的 read() 由调用方 finally 里的 reader.cancel() 收尾（不会泄漏）。
 */
async function readWithIdleTimeout(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  idleMs: number,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  if (idleMs <= 0) return reader.read();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`SSE 空闲超时（${idleMs}ms 无数据）`)), idleMs);
  });
  const read = reader.read();
  // 超时分支胜出后，pending 的 read 由调用方 cancel 收尾；它若以拒绝收场算「已处理」，
  // 避免变成 unhandled rejection（race 用的是原始 promise，这里只是挂一个空 handler）。
  void read.catch(() => {});
  try {
    return await Promise.race([read, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** GET 订阅回合事件流；断线按 Last-Event-ID 指数退避重连。 */
export async function streamTurnEvents(
  projectId: string, turnId: string, opts: StreamOptions
): Promise<void> {
  let lastId = opts.lastEventId ?? 0;
  let reconnects = 0;
  const maxReconnects = opts.maxReconnects ?? 5;
  const idleMs = opts.idleTimeoutMs ?? 45_000;
  const url = `/projects/${projectId}/turns/${turnId}/events`;
  // N14：只有收到明确终态（turn_done/error）才算完成；非终态 EOF（HTTP 200 后流被截断、
  // 未见终态）一律按断线恢复重连，绝不当正常关闭——否则 conversation 永远停在 turnRunning。
  let terminalSeen = false;

  while (true) {
    if (opts.signal?.aborted) return;
    // 本连接是否收到「新帧」（seq 前进）：仅此才算真进展、才清零退避计数，
    // 避免反复「200＋立即 EOF/重发旧帧」把重连次数掩盖成无限循环。
    let progress = false;
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream", ...authHeaders(), ...ownerHeaders() };
      if (lastId > 0) headers["Last-Event-ID"] = String(lastId);
      // T03：每次连接捕获认证身份/代次——迟到的旧身份 401 不得清掉后来建立的会话
      const token = getSessionToken();
      const epoch = getAuthEpoch();
      const resp = await fetch(url, { headers, signal: opts.signal });
      if (resp.status === 401) {
        // 401 是确定性认证失败（会话/令牌失效），退避重连不可能自愈：不走重连循环，
        // 构造 ApiError 走会话失效判定——命中则触发 R15 钩子（清会话+标失效，与 REST 通道同语义），
        // 无论何种 401 都立即 onError 终止。
        const err = new ApiError(401, await resp.text().catch(() => ""),
                                 isStaleSession(token));
        notifySessionExpiredIfAny(err, epoch);
        opts.onError?.(err);
        return;
      }
      if (!resp.ok || !resp.body) throw new Error(`SSE HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let closed = false;
      try {
        while (!closed) {
          const { value, done } = await readWithIdleTimeout(reader, idleMs);
          if (done) break;                // EOF：交下方终态判定（非终态则重连，不当完成）
          buffer += decoder.decode(value, { stream: true });
          const { frames, rest } = parseSseFrames(buffer);
          buffer = rest;
          for (const f of frames) {
            if (f.seq > lastId) { lastId = f.seq; progress = true; }   // 仅新帧推进算进展
            opts.onEvent(f);
            if (f.event === "turn_done" || f.event === "error") { terminalSeen = true; closed = true; }
          }
        }
      } finally {
        // 无论 turn_done/error 提前终止、流自然结束（done）还是 read 抛出，都取消 reader 释放底层连接：
        // 后端发完 turn_done 后仍阻塞在 queue.get() 等哨兵（心跳续命），不主动关流；客户端只是停止
        // 读取会让 TCP 连接滞留，多回合后耗尽浏览器同域并发上限（HTTP/1.1 ~6）导致前端卡死。
        // abort 场景已由 signal 断开，cancel 幂等安全，其可能的拒绝一并吞掉。
        await reader.cancel().catch(() => {});
      }
      if (terminalSeen) { opts.onClose?.(); return; }   // 明确终态：正常结束
      // 非终态 EOF：连接在收到终态前被截断（网络/代理/服务端重启），按断线恢复重连
      throw new Error("SSE 流在收到终态前结束");
    } catch (err) {
      if (opts.signal?.aborted) return;
      if (progress) reconnects = 0;        // 真正收到新帧才清零退避（断线恢复）；无进展则累加
      if (reconnects >= maxReconnects) { opts.onError?.(err as Error); return; }
      reconnects++;
      await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** reconnects, 8000)));
    }
  }
}
