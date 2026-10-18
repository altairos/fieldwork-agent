import { getToolCard, toolVisibility } from "../cards/registry";
import { EmptyState } from "../ui/EmptyState";
import { IconCompass } from "../ui/icons";
import { useConversationStore } from "../../stores/conversation";
import { useProjectStore } from "../../stores/project";
import { useToolsStore } from "../../stores/tools";
import { useUiStore } from "../../stores/ui";
import { AssistantStream } from "./AssistantStream";
import { CollapsedToolCard } from "./CollapsedToolCard";
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
 * 可见性分流（C 节问题 2）：settled 区按 `toolVisibility` 三级分流——full 原样整卡、
 * collapsed 折叠摘要条（CollapsedToolCard，点击展开原卡）、hidden 不显示（成功与失败均不显示，
 * 内部状态写入自会由助手正文解释）；未注册工具默认 full。成功活动不再进活动条（见
 * ToolActivityStrip），由这里的卡片承担结果呈现。
 *
 * 已知取舍：tools store 不记录调用属于哪个回合（Task 3 契约），故本回合区块内的结果卡按
 *   `tools.order` 全量渲染于时间线尾部活动区；多回合会话中历史卡片不会回到各自消息之间。
 *
 * 空态引导（可点性规范）：空对话时给出当前阶段的示例问题 chips，点击写入输入框
 * （ui store 的 composerDraft 单次通道，由 Composer 消费），不自动发送——用户可改写后再发。
 */

/** 阶段 → 示例问题（空对话引导；文案与该阶段右栏面板能力对应，不承诺内核没有的能力） */
const PHASE_EXAMPLES: Record<string, string[]> = {
  research_positioning: [
    "我想研究村落宗族组织的变迁，帮我定位学科与核心研究问题",
    "时间窗 10 天、经费 2 万，这个选题的规模可行吗",
    "帮我梳理这个选题需要回答的三个关键问题",
  ],
  field_site_profiles: [
    "帮我对比两个候选田野点的交通与住宿条件",
    "这个田野点有哪些文化禁忌需要注意",
    "为当前田野点补一份四维画像",
  ],
  itinerary_planning: [
    "按 10 天时间窗排一版行程草案",
    "行程太紧了，帮我加缓冲日",
    "预算超了，帮我压缩住宿开销",
  ],
  proposal_generation: [
    "基于当前行程生成八章方案草稿",
    "把访谈提纲要点并入方法论章节",
  ],
};
export function Timeline({
  onRetry,
  canRetry = false,
  retrying = false,
  incomplete = false,
}: {
  onRetry?: () => void;
  canRetry?: boolean;
  retrying?: boolean;
  incomplete?: boolean;
} = {}) {
  const messages = useConversationStore((s) => s.messages);
  const streamBuffer = useConversationStore((s) => s.streamBuffer);
  const turnRunning = useConversationStore((s) => s.turnRunning);
  const byCallId = useToolsStore((s) => s.byCallId);
  const order = useToolsStore((s) => s.order);
  const phase = useProjectStore((s) => s.phase);

  const settled = order
    .map((id) => byCallId[id])
    .filter((call) => Boolean(call) && call.status !== "running");
  // hidden 级不进时间线（含失败态：正文自会解释）；失败态同样不进 settled 卡片区
  // （问题4：工具未返回数据时空卡不滞留对话流，失败由活动条与助手正文承担）；
  // done 但数据为空的卡保留（NoData 对“成功但无数据”仍有信息量）。全部被过滤时不渲染外层缩进容器。
  const visible = settled.filter((call) =>
    (toolVisibility[call.tool] ?? "full") !== "hidden" && call.status !== "failed");
  const empty = messages.length === 0 && settled.length === 0 && !streamBuffer && !turnRunning;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto scroll px-6 py-5 space-y-5">
      {messages.map((m, i) => (
        <MessageBubble key={`${m.role}-${i}`} role={m.role} content={m.content} />
      ))}

      <ToolActivityStrip />
      <AssistantStream />

      {visible.length > 0 && (
        <div className="pl-9 space-y-3">
          {visible.map((call) => {
            if ((toolVisibility[call.tool] ?? "full") === "collapsed") {
              return (
                <CollapsedToolCard
                  key={call.call_id}
                  tool={call.tool}
                  data={call.data === undefined ? null : call.data}
                  degraded={call.degraded ?? null}
                  error={call.error ?? null}
                />
              );
            }
            const Card = getToolCard(call.tool);
            return (
              <Card
                key={call.call_id}
                data={call.data === undefined ? null : call.data}
                degraded={call.degraded ?? null}
                error={call.error ?? null}
              />
            );
          })}
        </div>
      )}

      {/* 回合中断：正文残缺/未收敛 → 给「重新生成」而不让用户自己重写问题 */}
      {(incomplete || canRetry) && (
        <div className="pl-9 flex items-center gap-2 flex-wrap text-[11.5px]">
          {incomplete && (
            <span className="text-ochre">本轮生成中断：正文可能在句中被截断，或未给出结论。</span>
          )}
          {canRetry && onRetry && (
            <button
              type="button"
              onClick={onRetry}
              disabled={retrying}
              className="chip hairline bg-card text-inksoft hover:bg-cardsunk hover:text-ink transition disabled:opacity-50"
            >
              {retrying ? "正在重新生成…" : "重新生成"}
            </button>
          )}
        </div>
      )}

      {empty && (
        <EmptyState
          icon={<IconCompass className="w-4 h-4" />}
          title="尚无对话"
          hint="在下方输入调研安排；罗盘按阶段推进，工具结果卡与右栏基本信息面板会随对话进展而更新"
        >
          <div className="flex flex-wrap justify-center gap-1.5 max-w-md">
            {(PHASE_EXAMPLES[phase] ?? PHASE_EXAMPLES.research_positioning).map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => useUiStore.getState().setComposerDraft(q)}
                className="chip hairline bg-card text-inksoft hover:bg-cardsunk hover:text-ink transition"
              >
                {q}
              </button>
            ))}
          </div>
        </EmptyState>
      )}
    </div>
  );
}
