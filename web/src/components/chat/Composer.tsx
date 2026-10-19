import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "../ui/Button";
import { IconAlert, IconLock, IconSend } from "../ui/icons";
import { useTurn } from "../../hooks/useTurn";
import { useUiStore } from "../../stores/ui";
import { useConversationStore } from "../../stores/conversation";
import { QuickReplies } from "./QuickReplies";

/**
 * Composer — 中栏输入框（两步式回合的用户侧入口）。
 *
 * 设计板同源：中栏 Composer lines 516-526
 *   外框 line 517 `border-t border-hairline bg-card px-6 py-3.5`
 *   输入壳 line 518 `hairline rounded-lg bg-paper/60 p-2.5 flex items-end gap-3 focus-within:border-cinnabar/50 transition`
 *   textarea line 519 `flex-1 bg-transparent resize-none outline-none text-[13.5px] leading-relaxed placeholder:text-inkfaint`
 *     （rows=1 + 随内容自增高，上限 160px，避免长文把时间线挤没；
 *     移动端批次 C：粗指针设备提到 16px，防 iOS Safari 聚焦自动缩放）
 *   发送键 line 520 `h-9 w-9 grid place-items-center rounded-md bg-cinnabar` → 复用 Task 5 `Button`
 *     primary（其配色即 line 520 的 bg-cinnabar / #F7EFE6 / hover #96382a，token 外值已在 Button 内注行号）
 *   底注 line 522-524：`flex items-center gap-3 mt-2 text-[10.5px] text-inkfaint`
 *     「阶段推进由可验证条件判定，非模型自由发挥」
 *     （原右侧「模型可配置 · function calling」易被误读为可自选 LLM，已删除）
 *
 * 交互：回车发送 / Shift+Enter 换行 / 输入法组合中（isComposing）不发送；busy 时发送键禁用并显示「取消」；
 * conflict（POST /messages 409）如实提示“已有回合在执行”（useTurn 的 conflict 状态），不重试、不静默吞错；
 * locked（页面级无百炼 key，由 Workspace 判定注入）时输入禁用 + lockHint 提示（可浏览不可发）；
 * keyMissing（发送时后端 403 llm_key_required 兜底）同样提示回首页配置。
 *
 * turn 句柄由 App 的 `useTurn(projectId)` 注入（单一回合所有者）；仅传 projectId 时组件自建句柄，
 * 便于独立渲染/测试。两者取其一，绝不并存两个活动回合。
 *
 * 快捷作答（B 节问题 4）：末条 assistant 携带选项时输入壳上方渲染 QuickReplies chips；
 * 点选即发送、自定义聚焦输入框；用户作答（pushUser）/回合运行中/锁定时不显示。
 */
export interface TurnHandle {
  send: (text: string) => void | Promise<void>;
  cancel: () => void | Promise<void>;
  busy: boolean;
  conflict: boolean;
  keyMissing?: boolean;
}

const MAX_HEIGHT_PX = 160;

export function Composer({
  turn,
  projectId,
  locked = false,
  lockHint,
}: {
  turn?: TurnHandle;
  projectId?: string;
  /** true = 可浏览不可发（无百炼 key）：textarea 禁用，提示 lockHint */
  locked?: boolean;
  /** 锁定提示内容（Workspace 提供，含回首页链接） */
  lockHint?: ReactNode;
}) {
  const own = useTurn(projectId ?? "");
  const api: TurnHandle = turn ?? own;
  const [text, setText] = useState("");
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const quickReplies = useConversationStore((s) => s.quickReplies);

  // 自增高：先归零再取 scrollHeight（jsdom 下 scrollHeight=0，退化为单行高度，无副作用）
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [text]);

  // 示例 chip 草稿消费（ui store 单次通道）：写入输入框并聚焦，不自动发送；
  // locked 时 textarea disabled——受控 setText 无害、focus 无效不报错，解锁后文本保留
  const draft = useUiStore((s) => s.composerDraft);
  useEffect(() => {
    if (!draft) return;
    setText(draft);
    areaRef.current?.focus();
    useUiStore.getState().setComposerDraft(null);
  }, [draft]);

  const canSend = text.trim() !== "" && !api.busy && !locked;

  const submit = () => {
    const value = text.trim();
    if (!value || api.busy || locked) return;
    void api.send(value);
    setText("");
  };

  return (
    <div className="border-t border-hairline bg-card px-4 sm:px-6 py-3.5">
      {quickReplies.length > 0 && !api.busy && !locked && (
        <div className="mb-2">
          <QuickReplies
            options={quickReplies}
            onPick={(opt) => void api.send(opt)}
            onCustom={() => areaRef.current?.focus()}
          />
        </div>
      )}
      <div className="hairline rounded-lg bg-paper/60 p-2.5 flex items-end gap-3 focus-within:border-cinnabar/50 transition">
        <textarea
          ref={areaRef}
          rows={1}
          value={text}
          aria-label="消息输入"
          aria-disabled={locked}
          disabled={locked}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
            e.preventDefault();
            submit();
          }}
          className="flex-1 bg-transparent resize-none outline-none text-[13.5px] pointer-coarse:text-[16px] leading-relaxed placeholder:text-inkfaint max-h-[160px] disabled:text-inkfaint"
          placeholder={locked ? "配置百炼 API Key 后可开始对话…" : "继续描述你的调研安排，或让罗盘细化某一段行程…"}
        />
        {api.busy && (
          <Button variant="outline" size="sm" onClick={() => void api.cancel()} className="shrink-0">
            取消
          </Button>
        )}
        <Button
          variant="primary"
          aria-label="发送"
          disabled={!canSend}
          onClick={submit}
          className="w-9 !px-0 shrink-0"
        >
          <IconSend className="w-5 h-5 shrink-0" />
        </Button>
      </div>

      {api.conflict && (
        <div className="flex items-center gap-1.5 mt-2 text-[11.5px] text-ochre">
          <IconAlert className="w-3.5 h-3.5 shrink-0" />
          <span>已有回合在执行：请等待其完成，或先取消当前回合再发送。</span>
        </div>
      )}

      {(locked || api.keyMissing) && (
        <div className="flex items-center gap-1.5 mt-2 text-[11.5px] text-ochre" data-testid="composer-lock-hint">
          <IconLock className="w-3.5 h-3.5 shrink-0" />
          {locked
            ? lockHint
            : <span>缺少百炼 API Key：请回首页「密钥」填写并保存后重试。</span>}
        </div>
      )}

      {/* 底注：辅助说明（窄屏 flex-wrap 防挤溢） */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-[10.5px] text-inkfaint">
        <span>阶段推进由可验证条件判定，非模型自由发挥</span>
      </div>
    </div>
  );
}
