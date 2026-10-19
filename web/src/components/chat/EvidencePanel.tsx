import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { rest, type CachedSearchRecord, type CachedSearchSummary } from "../../api/rest";
import { evidenceTurnLabel, mergeEvidence, type EvidenceRow } from "../../lib/evidenceFilter";
import { useConversationStore } from "../../stores/conversation";
import { useToolsStore, type ToolCall } from "../../stores/tools";
import { getToolCard } from "../cards/registry";
import { toolLabel } from "../../lib/toolMeta";
import { IconCaret } from "../ui/icons";

/**
 * EvidencePanel — 中栏「检索结果」tab（spec C1/C5/C8）。
 * 混合数据源：tools store 实时层（同回合）+ rest.listCachedSearches 索引（跨回合）。
 * 合并规则见 lib/evidenceFilter（跨层配对/同题折叠/服务端时间轴，R13/R11/R15）。
 * 重拉时机：挂载时 + 回合结束（turnRunning true→false）。
 */
export function EvidencePanel({ projectId }: { projectId: string }) {
  const [summaries, setSummaries] = useState<CachedSearchSummary[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [records, setRecords] = useState<Record<string, CachedSearchRecord>>({});
  // 分字段订阅（评审 R5）：原先 useToolsStore() 无 selector，任何工具事件都重渲染整面板；
  // 且 useMemo 依赖整个 store 对象 → 每次必然失效，等于没 memo。
  const order = useToolsStore((s) => s.order);
  const byCallId = useToolsStore((s) => s.byCallId);
  const turnRunning = useConversationStore((s) => s.turnRunning);
  const currentTurnId = useConversationStore((s) => s.currentTurnId);
  const turnRunningRef = useRef(turnRunning);
  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;

  const reload = useCallback(async () => {
    try {
      const list = await rest.listCachedSearches(projectId);
      if (projectIdRef.current === projectId) {
        setSummaries(list);
        setLoadFailed(false);
      }
    } catch {
      if (projectIdRef.current === projectId) setLoadFailed(true);
    }
  }, [projectId]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    if (turnRunningRef.current && !turnRunning) void reload();  // 回合结束刷新（C5）
    turnRunningRef.current = turnRunning;
  }, [turnRunning, reload]);

  const rows = useMemo(() => {
    const settled = order
      .map((cid) => byCallId[cid])
      .filter((t): t is ToolCall => Boolean(t));
    return mergeEvidence(summaries, settled);
  }, [summaries, order, byCallId]);

  const toggle = (row: EvidenceRow) => {
    const next = !open[row.key];
    setOpen((prev) => ({ ...prev, [row.key]: next }));
    if (next && row.origin === "cache" && !records[row.key]) {
      rest.getCachedSearch(projectId, row.key)
        .then((rec) => setRecords((prev) => ({ ...prev, [row.key]: rec })))
        .catch(() => {
          // 单行失败只影响该行（C8 轻提示）；不动索引层状态
          setOpen((prev) => ({ ...prev, [row.key]: false }));
          setLoadFailed(true);
        });
    }
  };

  if (rows.length === 0 && !loadFailed) {
    return (
      <div className="flex-1 overflow-y-auto scroll">
        <div className="hairline rounded-lg bg-card px-3 py-6 text-center">
          <p className="text-[12.5px] text-inksoft">暂无可展示的检索结果</p>
          <p className="mt-1 text-[11.5px] text-inkfaint">
            随对话推进，工具检索证据会自动归档在此；内部管线与空结果已自动过滤。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto scroll px-3 py-3 space-y-2">
      {loadFailed && (
        <p className="hairline rounded-lg bg-card px-3 py-2 text-[11.5px] text-inksoft">
          检索记录加载失败，请稍后重试；以下仅显示本轮实时结果。
        </p>
      )}
      {rows.map((row) => {
        const rec = records[row.key];
        const truncated = row.truncated === true
          || (rec?.result as { truncated?: boolean } | undefined)?.truncated === true;
        return (
          <div key={row.key} className="hairline rounded-lg bg-card">
            <button
              type="button"
              onClick={() => toggle(row)}
              aria-expanded={open[row.key] ?? false}
              className="w-full flex items-center gap-2 px-3 py-2 text-left cursor-pointer"
            >
              <IconCaret
                className={`w-3.5 h-3.5 shrink-0 text-inkfaint transition-transform ${
                  open[row.key] ? "rotate-90" : ""}`}
              />
              <span className="text-[12.5px] text-inksoft shrink-0">{toolLabel(row.tool)}</span>
              <span className="flex-1 min-w-0 truncate text-[12.5px] text-ink">
                {row.query || "（未命名查询）"}
              </span>
              <span className="text-[11px] text-inkfaint shrink-0">
                {truncated ? "已截断" : evidenceTurnLabel(row, currentTurnId)}
              </span>
            </button>
            {open[row.key] && (
              row.origin === "live" ? (
                <LiveDetail callId={row.callId} />
              ) : !rec ? (
                <div className="px-3 pb-3 text-[12px] text-inkfaint">加载中…</div>
              ) : truncated ? (
                <div className="px-3 pb-3">
                  <p className="text-[11.5px] text-inkfaint">
                    截断：原始结果超出 64KB 缓存上限，仅保留前 500 字预览（R5）。
                  </p>
                  <pre className="mt-2 whitespace-pre-wrap break-all text-[11.5px] text-inksoft">
                    {String((rec.result as { preview?: unknown }).preview ?? "（无预览）")}
                  </pre>
                </div>
              ) : (
                (() => { const Card = getToolCard(row.tool); return <Card data={rec.result} />; })()
              )
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 实时行详情：直接读 tools store 的 data/degraded（同源数据红线：不重复请求）。
 *  failed 行已在 mergeEvidence 准入剔除（C7-3——失败可见性由对话 tab 活动条
 *  承担），此处只会渲染 done 行，无需 error 分支。 */
function LiveDetail({ callId }: { callId?: string }) {
  const call = useToolsStore((s) => s.byCallId[callId ?? ""]);
  if (!call) return null;
  const Card = getToolCard(call.tool);
  return (
    <div className="px-3 pb-3">
      <Card data={call.data} degraded={call.degraded} />
    </div>
  );
}
