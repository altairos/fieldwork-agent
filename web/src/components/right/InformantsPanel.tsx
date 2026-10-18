import { useState } from "react";
import { Chip } from "../ui/Chip";
import { EmptyState } from "../ui/EmptyState";
import { IconAlert, IconUsers } from "../ui/icons";
import { InformantCard } from "../cards/InformantCard";
import { boolOf, strOf } from "../cards/data";
import { useProjectStore } from "../../stores/project";
import { useToolsStore, type ToolCall } from "../../stores/tools";
import { rest } from "../../api/rest";

/**
 * InformantsPanel — 右栏「访谈对象」面板（实时状态）。
 *
 * 设计板同源：右栏访谈对象面板 lines 638-645
 *   标题 line 639 `font-serif text-[15px] font-600 mb-3`「访谈对象 · 4」
 *   候选/报道人小卡 line 641 `hairline rounded-lg bg-card p-3`：
 *     头部 `flex items-center justify-between`（左 `num text-[12px] text-cinnabar` 编号，
 *     右 `chip bg-cardsunk text-inksoft hairline` 类型）+ 正文 `text-[12.5px] text-inksoft mt-1.5`
 *     + 伦理行 `text-[11px] text-inkfaint mt-1`
 *   节标签沿用中栏工具活动条 line 444 的 `text-[10.5px] tracking-[.16em] text-inkfaint`
 *
 * 同源红线②：已登记报道人取 `snapshot.informants`（REST 初始快照，InformantRecord 五字段）；
 * 候选取本回合 `suggest_informants` 的 `tool_completed.data`（tools store），**直接复用 Task 6 的
 * `InformantCard`**（中栏同一组件、同一 payload），面板不重写六字段渲染、不另起请求、不转述数字。
 * `research_context.interviews_not_applicable` 为真时如实呈现内核给的 `interviews_na_reason`
 * （非访谈型研究显式跳过访谈对象硬条件），不编造报道人。
 */
export function InformantsPanel() {
  const snapshot = useProjectStore((s) => s.snapshot);
  const byCallId = useToolsStore((s) => s.byCallId);
  const order = useToolsStore((s) => s.order);

  const records = snapshot?.informants ?? [];
  const ctx = snapshot?.research_context ?? {};
  const notApplicable = boolOf(ctx.interviews_not_applicable);
  const naReason = strOf(ctx.interviews_na_reason);

  // 最新一次 suggest_informants（done/failed 都呈现：失败时卡片如实说明未取得候选）
  let candidateCall: ToolCall | null = null;
  for (const id of order) {
    const call = byCallId[id];
    if (call && call.tool === "suggest_informants" && call.status !== "running") candidateCall = call;
  }

  const [adopting, setAdopting] = useState<number | null>(null);
  const [adoptError, setAdoptError] = useState<string | null>(null);

  // 采纳登记：POST /informants（候选原样上行）→ 刷新快照（右栏为快照投影，不局部改）
  const adopt = async (candidate: Record<string, unknown>, i: number) => {
    const pid = snapshot?.project_id;
    if (!pid || adopting !== null) return;
    setAdopting(i);
    setAdoptError(null);
    try {
      await rest.adoptInformant(pid, candidate);
      useProjectStore.getState().loadSnapshot(await rest.getProject(pid));
    } catch (e) {
      setAdoptError(e instanceof Error ? e.message : String(e));
    } finally {
      setAdopting(null);
    }
  };

  const naNotice = notApplicable && (
    <div className="hairline rounded-lg bg-ochrewash p-3 mb-3 flex items-start gap-2 text-[11.5px]">
      <IconAlert className="w-4 h-4 text-ochre shrink-0 mt-0.5" />
      <span className="text-inksoft flex-1 min-w-0">
        本研究已标记为不适用访谈对象{naReason ? `：${naReason}` : ""}（内核显式跳过该项硬条件）。
      </span>
    </div>
  );

  if (records.length === 0 && candidateCall === null) {
    return (
      <div>
        {naNotice}
        <EmptyState
          icon={<IconUsers className="w-4 h-4" />}
          title="尚无访谈对象"
          hint="在中栏说明研究主题与学科，罗盘会给出候选类型（含接近策略与伦理提示）"
        />
      </div>
    );
  }

  return (
    <div>
      <div className="font-serif text-[15px] font-semibold mb-3">{`访谈对象 · ${records.length}`}</div>
      {naNotice}

      {records.length > 0 && (
        <div className="mb-4">
          <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">已登记报道人</div>
          <div className="space-y-2">
            {records.map((r, i) => {
              const code = strOf(r.code);
              const type = strOf(r.type);
              const relation = strOf(r.relation);
              const contact = strOf(r.contact);
              const notes = strOf(r.notes);
              return (
                <div key={`${code}-${i}`} className="hairline rounded-lg bg-card p-3">
                  {/* 头部：编号 + 类型（设计板 line 641） */}
                  <div className="flex items-center justify-between gap-2">
                    <span className="num text-[12px] text-cinnabar">{code || `#${i + 1}`}</span>
                    {type && <Chip tone="neutral">{type}</Chip>}
                  </div>
                  {relation && <div className="text-[12.5px] text-inksoft mt-1.5">{relation}</div>}
                  {contact && (
                    <div className="text-[11.5px] text-inkfaint mt-1">{`联系方式 ${contact}`}</div>
                  )}
                  {notes && <div className="text-[11px] text-inkfaint mt-1">{notes}</div>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {candidateCall !== null && (
        <div>
          <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">
            候选 · 未登记
            {adoptError && <span className="text-ochre ml-2 normal-case">{adoptError}</span>}
          </div>
          {/* 同源复用：与中栏时间线同一组件 + 同一 tool_completed.data；cardActions 注入采纳 */}
          <InformantCard
            data={candidateCall.data === undefined ? null : candidateCall.data}
            degraded={candidateCall.degraded ?? null}
            cardActions={(candidate, i) => (
              <button
                type="button"
                disabled={adopting !== null}
                onClick={() => adopt(candidate, i)}
                className="text-[11.5px] text-cinnabar hairline rounded-md px-2.5 py-1 hover:bg-cardsunk disabled:opacity-50"
              >
                {adopting === i ? "登记中…" : "采纳登记"}
              </button>
            )}
          />
        </div>
      )}
    </div>
  );
}
