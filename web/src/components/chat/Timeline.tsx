import { getToolCard } from "../cards/registry";
import { EmptyState } from "../ui/EmptyState";
import { IconCompass } from "../ui/icons";
import { useConversationStore } from "../../stores/conversation";
import { useToolsStore } from "../../stores/tools";
import { AssistantStream } from "./AssistantStream";
import { MessageBubble } from "./MessageBubble";
import { ToolActivityStrip } from "./ToolActivityStrip";

/**
 * Timeline — 中栏对话时间线（滚动容器）。
 *
 * 设计板同源：中栏 lines 433-514
 *   容器 line 433 `flex-1 overflow-y-auto scroll px-6 py-5 space-y-5`
 *   顺序：用户/助手历史消息（lines 436-440 / 450-462）→ 工具活动条（lines 443-448）→
 *         助手流式正文（lines 450-462）→ 工具结果卡（lines 464-511，缩进与助手正文对齐 pl-9
 *         = 头像 w-6 24px + gap-3 12px，即设计板把结果卡嵌在助手块 line 453 `min-w-0 flex-1` 内的同一列）
 *
 * 同源红线②：结果卡一律 `getToolCard(call.tool)` + `tool_completed.data`（tools store 归约结果），
 * 时间线自身 0 处 fetch、0 处数字加工；运行中的调用不提前渲染卡片（否则会误报「工具未返回数据」）。
 *
 * 已知取舍：tools store 不记录调用属于哪个回合（Task 3 契约），故本回合区块内的结果卡按
 * `tools.order` 全量渲染于时间线尾部活动区；多回合会话中历史卡片不会回到各自消息之间。
 */
export function Timeline() {
  const messages = useConversationStore((s) => s.messages);
  const streamBuffer = useConversationStore((s) => s.streamBuffer);
  const turnRunning = useConversationStore((s) => s.turnRunning);
  const byCallId = useToolsStore((s) => s.byCallId);
  const order = useToolsStore((s) => s.order);

  const settled = order
    .map((id) => byCallId[id])
    .filter((call) => Boolean(call) && call.status !== "running");
  const empty = messages.length === 0 && settled.length === 0 && !streamBuffer && !turnRunning;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto scroll px-6 py-5 space-y-5">
      {messages.map((m, i) => (
        <MessageBubble key={`${m.role}-${i}`} role={m.role} content={m.content} />
      ))}

      <ToolActivityStrip />
      <AssistantStream />

      {settled.length > 0 && (
        <div className="pl-9 space-y-3">
          {settled.map((call) => {
            const Card = getToolCard(call.tool);
            return (
              <Card
                key={call.call_id}
                data={call.data === undefined ? null : call.data}
                degraded={call.degraded ?? null}
              />
            );
          })}
        </div>
      )}

      {empty && (
        <EmptyState
          icon={<IconCompass className="w-4 h-4" />}
          title="尚无对话"
          hint="在下方输入调研安排；罗盘按阶段推进，工具结果卡与右栏状态投影会随回合更新"
        />
      )}
    </div>
  );
}
