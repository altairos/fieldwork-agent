import type { ReactNode } from "react";
import { Chip } from "../ui/Chip";
import { IconUsers } from "../ui/icons";
import { CardShell, NoData } from "./CardShell";
import { asArray, asRecord, strOf } from "./data";
import type { ToolCardProps } from "./types";

/**
 * InformantCard — 访谈对象候选卡（`suggest_informants` → InformantCandidate[]）。
 *
 * 设计板同源：右栏访谈对象面板 lines 638-645 的候选小卡
 *   `hairline rounded-lg bg-card p-3` + 头部 `flex items-center justify-between`
 *   （左 `num text-[12px] text-cinnabar` 编号 P1，右 `chip bg-cardsunk text-inksoft hairline` 类型）
 *   + 正文 `text-[12.5px] text-inksoft mt-1.5` + 伦理行 `text-[11px] text-inkfaint mt-1`；
 *   提问域用 indigo wash chip（line 602 文化图谱同款）。
 *
 * data 形状：内核直接返回**裸数组**（kernel/documents/informants.py L84-103），
 * 故同时兼容 `{candidates|items|value: [...]}` 包装（宿主落盘/未来变更的容错），
 * 但**不臆造字段**：七字段（type/approach/access_channel/question_domains/cautions/
 * ethics/preparation）逐个如实呈现，空字段整行不渲染。
 * 无 llm 降级时内核给 degraded="参考起点，非完整覆盖" → 由 CardShell 头部 ochre chip 承担。
 */

/** 字段 → 中文行标（顺序即呈现顺序：可触达性在前，伦理在后）。 */
const FIELDS: { key: string; label: string; tone?: "ochre" | "soft" | "faint" }[] = [
  { key: "approach", label: "接触方式", tone: "soft" },
  { key: "access_channel", label: "进入渠道", tone: "soft" },
  { key: "preparation", label: "访前准备", tone: "soft" },
  { key: "cautions", label: "注意事项", tone: "ochre" },
  { key: "ethics", label: "伦理", tone: "faint" },
];

/** data → 候选数组（裸数组优先，其次常见包装键）。导出供右栏面板判空复用（单一来源）。 */
export function toCandidates(data: unknown): Record<string, unknown>[] {
  if (Array.isArray(data)) return asArray(data);
  const rec = asRecord(data);
  for (const key of ["candidates", "items", "value"]) {
    if (Array.isArray(rec[key])) return asArray(rec[key]);
  }
  return [];
}

/** cardActions：右栏面板注入的每卡操作区（中栏时间线不传，渲染逐字节不变）。 */
interface InformantCardProps extends ToolCardProps {
  cardActions?: (candidate: Record<string, unknown>, index: number) => ReactNode;
}

export function InformantCard({ data, degraded, cardActions }: InformantCardProps) {
  const candidates = toCandidates(data);
  const statusChip =
    candidates.length > 0 ? <Chip tone="neutral">{`${candidates.length} 类`}</Chip> : undefined;

  return (
    <CardShell
      title="访谈对象候选"
      icon={<IconUsers className="w-4 h-4" />}
      degraded={degraded}
      statusChip={statusChip}
    >
      {candidates.length === 0 ? (
        <NoData hint="未取得访谈对象候选；可重试 suggest_informants，或先补充研究主题与学科。" />
      ) : (
        <div className="space-y-2">
          {candidates.map((raw, i) => {
            const c = asRecord(raw);
            const type = strOf(c.type);
            const domains = asArray(c.question_domains)
              .map((d) => strOf(d))
              .filter((d) => d !== "");
            return (
              <div key={`${type}-${i}`} className="hairline rounded-lg bg-card p-3">
                {/* 头部：编号 + 类型（设计板 line 641） */}
                <div className="flex items-center justify-between gap-2">
                  <span className="num text-[12px] text-cinnabar">{`P${i + 1}`}</span>
                  {type && <Chip tone="neutral">{type}</Chip>}
                </div>

                {/* 提问域：indigo chip 组（设计板 line 602） */}
                {domains.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {domains.map((d, j) => (
                      <Chip key={`${d}-${j}`} tone="indigo">
                        {d}
                      </Chip>
                    ))}
                  </div>
                )}

                {/* 其余字段：标签 + 原文，空字段不渲染（不编造内容） */}
                <dl className="mt-2 space-y-1">
                  {FIELDS.map((f) => {
                    const value = strOf(c[f.key]);
                    if (!value) return null;
                    const color =
                      f.tone === "ochre"
                        ? "text-[12px] text-ochre"
                        : f.tone === "faint"
                          ? "text-[11px] text-inkfaint"
                          : "text-[12.5px] text-inksoft";
                    return (
                      <div key={f.key} className="flex items-baseline gap-2">
                        <dt className="text-[10.5px] text-inkfaint shrink-0 w-14">{f.label}</dt>
                        <dd className={`flex-1 min-w-0 ${color}`}>{value}</dd>
                      </div>
                    );
                  })}
                </dl>
                {cardActions && (
                  <div className="mt-2 flex items-center gap-2">{cardActions(c, i)}</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </CardShell>
  );
}
