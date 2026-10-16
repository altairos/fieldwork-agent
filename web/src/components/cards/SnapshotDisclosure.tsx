import { useState } from "react";
import { rest } from "../../api/rest";
import { useProjectStore } from "../../stores/project";

/** Task 11 source_ref 约定："{条目名}（flyai 快照 {cid}（{date} 查询））"。 */
const SNAPSHOT_CID = /flyai 快照 (srch_\w+)/;

/** 提取快照 cid；非快照计价（"MU5843"、"G29（flyai 实时报价）"）返回 null。 */
export function snapshotCid(sourceRef: string): string | null {
  const m = SNAPSHOT_CID.exec(sourceRef);
  return m ? m[1] : null;
}

/**
 * SnapshotDisclosure — 预算条目的报价证据懒加载披露（spec §5.2；红线③可复核）。
 *
 * 点击才请求 `GET /projects/{pid}/cached-searches/{cid}`（平时零请求）；
 * 无 cid（条目非快照计价）或无 projectId（卡片脱离项目预览）时整体不渲染，
 * 父组件可无条件挂载。
 */
export function SnapshotDisclosure({ sourceRef }: { sourceRef: string }) {
  const cid = snapshotCid(sourceRef);
  const projectId = useProjectStore((s) => s.projectId);
  const [state, setState] = useState<"idle" | "loading" | "open" | "error">("idle");
  const [items, setItems] = useState<{ name?: string; price?: number | null }[]>([]);
  const [query, setQuery] = useState("");

  if (!cid || !projectId) return null;

  const reveal = async () => {
    if (state !== "idle") return;
    setState("loading");
    try {
      const rec = await rest.getCachedSearch(projectId, cid);
      setQuery(rec.query ?? "");
      setItems(rec.result?.items ?? []);
      setState("open");
    } catch {
      setState("error"); // 404/网络失败：如实说明，不抛错（红线②）
    }
  };

  return (
    <div className="text-[10.5px]">
      {state === "idle" && (
        <button type="button" onClick={reveal} className="text-ochre underline underline-offset-2">
          查看报价快照证据
        </button>
      )}
      {state === "loading" && <span className="text-inkfaint">快照载入中…</span>}
      {state === "error" && (
        <span className="text-inkfaint">快照不可用（可能已过期），请以现场复核报价为准</span>
      )}
      {state === "open" && (
        <div className="rounded border border-hairline bg-cardsunk/50 px-2 py-1.5">
          {query && <div className="text-inkfaint truncate">{query}</div>}
          {items.length === 0 ? (
            <div className="text-inkfaint">快照无条目</div>
          ) : (
            <ul>
              {items.slice(0, 5).map((it, i) => (
                <li key={i} className="truncate">
                  · {it.name || "未命名"}
                  {typeof it.price === "number" ? ` ¥${it.price}` : "（无报价）"}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
