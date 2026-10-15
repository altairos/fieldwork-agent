import type { EventEnvelope, SseEvent } from "../types/events";
import { useToolsStore } from "../stores/tools";
import { useConversationStore } from "../stores/conversation";
import { useProjectStore } from "../stores/project";
import { useUiStore } from "../stores/ui";

/** envelope.data 平铺整包 → 判别联合 SseEvent（补 event 字段）。 */
function toSseEvent(env: EventEnvelope): SseEvent {
  return { ...(env.data as object), event: env.event } as SseEvent;
}

/** 按 seq 去重后把一个事件喂给四个 store；返回更新后的已见 seq 集合。 */
export function dispatchEvent(env: EventEnvelope, seen: Set<number>): Set<number> {
  if (seen.has(env.seq)) return seen;       // 重连补发的重复帧：跳过
  const next = new Set(seen); next.add(env.seq);
  const ev = toSseEvent(env);
  useToolsStore.getState().applyEvent(ev);
  useConversationStore.getState().applyEvent(ev);
  useProjectStore.getState().applyEvent(ev);
  useUiStore.getState().applyEvent(ev);
  return next;
}
