import type { ProposalMeta } from "../../api/rest";

/**
 * VersionSwitch — 提案版本切换 chip 组（draft vN / final）。
 * 设计板同源：view-proposal 左栏版本区（lines 688-695）：
 *   非当前 `chip bg-card hairline text-inkfaint`（lines 691-692）
 *   当前/final `chip bg-cinnabarwash text-cinnabar border-cinnabar/25`（line 693）
 * 版本列表来自 GET /projects/{id}/proposals（rest.listProposals）。
 * 同一 version 值出现多次（多份 draft）时按出现次序标 `draft vN` 以消歧。
 */
export function VersionSwitch({
  versions,
  current,
  onSelect,
}: {
  versions: ProposalMeta[];
  /** 当前选中的版本 id（null = 尚无可显示版本） */
  current: string | null;
  onSelect: (id: string) => void;
}) {
  const seen: Record<string, number> = {};
  return (
    <div className="flex flex-wrap gap-1">
      {versions.map((v) => {
        const key = `${v.kind ?? "proposal"}:${v.version ?? "draft"}`;
        seen[key] = (seen[key] ?? 0) + 1;
        const label =
          v.version === "final" ? "final" : `${v.version ?? "draft"} v${seen[key]}`;
        const active = v.id === current;
        return (
          <button
            key={v.id}
            type="button"
            aria-pressed={active}
            title={v.created_at ? `保存于 ${v.created_at}` : undefined}
            onClick={() => onSelect(v.id)}
            className={`chip transition ${
              active
                ? "bg-cinnabarwash text-cinnabar border-cinnabar/25"
                : "bg-card hairline text-inkfaint hover:bg-cardsunk"
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
