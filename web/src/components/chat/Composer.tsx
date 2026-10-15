import { useEffect, useRef, useState } from "react";
import { Button } from "../ui/Button";
import { IconAlert, IconSend } from "../ui/icons";
import { useTurn } from "../../hooks/useTurn";

/**
 * Composer — 中栏输入框（两步式回合的用户侧入口）。
 *
 * 设计板同源：中栏 Composer lines 516-526
 *   外框 line 517 `border-t border-hairline bg-card px-6 py-3.5`
 *   输入壳 line 518 `hairline rounded-lg bg-paper/60 p-2.5 flex items-end gap-3 focus-within:border-cinnabar/50 transition`
 *   textarea line 519 `flex-1 bg-transparent resize-none outline-none text-[13.5px] leading-relaxed placeholder:text-inkfaint`
 *     （rows=1 + 随内容自增高，上限 160px，避免长文把时间线挤没）
 *   发送键 line 520 `h-9 w-9 grid place-items-center rounded-md bg-cinnabar` → 复用 Task 5 `Button`
 *     primary（其配色即 line 520 的 bg-cinnabar / #F7EFE6 / hover #96382a，token 外值已在 Button 内注行号）
 *   底注 line 522-524：`flex items-center gap-3 mt-2 text-[10.5px] text-inkfaint`
 *     「阶段推进由可验证条件判定，非模型自由发挥」+ 右侧 `ml-auto`「qwen3.8-flash · function calling」
 *     （模型名为 core/config.py L32 `FA_INTERVIEW_MODEL` 的缺省值，可被环境变量覆盖）
 *
 * 交互：回车发送 / Shift+Enter 换行 / 输入法组合中（isComposing）不发送；busy 时发送键禁用并显示「取消」；
 * conflict（POST /messages 409）如实提示"已有回合在执行"（useTurn 的 conflict 状态），不重试、不静默吞错。
 *
 * turn 句柄由 App 的 `useTurn(projectId)` 注入（单一回合所有者）；仅传 projectId 时组件自建句柄，
 * 便于独立渲染/测试。两者取其一，绝不并存两个活动回合。
 */
export interface TurnHandle {
  send: (text: string) => void | Promise<void>;
  cancel: () => void | Promise<void>;
  busy: boolean;
  conflict: boolean;
}

const MAX_HEIGHT_PX = 160;

export function Composer({ turn, projectId }: { turn?: TurnHandle; projectId?: string }) {
  const own = useTurn(projectId ?? "");
  const api: TurnHandle = turn ?? own;
  const [text, setText] = useState("");
  const areaRef = useRef<HTMLTextAreaElement>(null);

  // 自增高：先归零再取 scrollHeight（jsdom 下 scrollHeight=0，退化为单行高度，无副作用）
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [text]);

  const canSend = text.trim() !== "" && !api.busy;

  const submit = () => {
    const value = text.trim();
    if (!value || api.busy) return;
    void api.send(value);
    setText("");
  };

  return (
    <div className="border-t border-hairline bg-card px-6 py-3.5">
      <div className="hairline rounded-lg bg-paper/60 p-2.5 flex items-end gap-3 focus-within:border-cinnabar/50 transition">
        <textarea
          ref={areaRef}
          rows={1}
          value={text}
          aria-label="消息输入"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
            e.preventDefault();
            submit();
          }}
          className="flex-1 bg-transparent resize-none outline-none text-[13.5px] leading-relaxed placeholder:text-inkfaint max-h-[160px]"
          placeholder="继续描述你的调研安排，或让罗盘细化某一段行程…"
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
          className="w-9 px-0 shrink-0"
        >
          <IconSend className="w-4 h-4" />
        </Button>
      </div>

      {api.conflict && (
        <div className="flex items-center gap-1.5 mt-2 text-[11.5px] text-ochre">
          <IconAlert className="w-3.5 h-3.5 shrink-0" />
          <span>已有回合在执行：请等待其完成，或先取消当前回合再发送。</span>
        </div>
      )}

      <div className="flex items-center gap-3 mt-2 text-[10.5px] text-inkfaint">
        <span>阶段推进由可验证条件判定，非模型自由发挥</span>
        <span className="ml-auto num">qwen3.8-flash · function calling</span>
      </div>
    </div>
  );
}
