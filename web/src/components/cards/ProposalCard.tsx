import { Chip } from "../ui/Chip";
import { IconBook, IconCaret, IconDownload } from "../ui/icons";
import { rest } from "../../api/rest";
import { CardShell, NoData, Notice } from "./CardShell";
import { asArray, asRecord, strOf } from "./data";
import type { ToolCardProps } from "./types";

/**
 * ProposalCard — 文档生成卡（`compose_proposal` → ProposalDoc；`compose_interview_guide`
 * → `{类型: 提纲正文}` 字典，kernel/documents/proposal.py L455-458）。两种 payload 共用一卡
 * （plan doc lines 143-154：compose_proposal / compose_interview_guide → 文档卡）。
 *
 * 设计板同源：
 *   - ProposalView 版本区 lines 688-690（版本 chip 语气）与八章导航（sections 逐章 chip）
 *   - 按钮 lines 243-244：「下载 .md」= `rounded-md hairline bg-card text-ink hover:bg-cardsunk`，
 *     「查看」= `rounded-md text-indigo hover:bg-indigowash`（此处按卡内尺度收到 h-8/text-[12.5px]）
 *   - 提纲正文用 `font-serif` 纸面语气（line 454），保留换行不重排
 *
 * 红线②：卡片不订阅 store、不发请求——`projectId` 由调用方（Task 7/8 时间线）注入，
 * 缺省时只说明"无法生成链接"，绝不猜项目 id；`requested_mode ≠ mode` 时如实声明体例限制
 * （不粉饰为已按用户体例生成）。markdown 是唯一真源，卡内只给元信息与入口，不改写正文。
 */

interface ProposalCardProps extends ToolCardProps {
  /** 项目 id（调用方注入）；缺省则不渲染查看/下载链接 */
  projectId?: string;
}

/** kind → 中文卡名（不回显英文枚举）。 */
const KIND: Record<string, string> = {
  proposal: "研究方案",
  interview_guide: "访谈提纲",
  guide: "访谈提纲",
};

/** 章节标题去掉 markdown 井号前缀（内核 sections 形如 `## 一、选题依据`）。 */
function sectionLabel(raw: string): string {
  return raw.replace(/^#+\s*/, "").trim();
}

export function ProposalCard({ data, degraded, projectId }: ProposalCardProps) {
  const rec = asRecord(data);
  const markdown = strOf(rec.markdown);
  const kind = strOf(rec.kind);
  const version = strOf(rec.version);
  const mode = strOf(rec.mode);
  const requestedMode = strOf(rec.requested_mode);
  const inputsHash = strOf(rec.inputs_hash);
  const sections = asArray(rec.sections).map((s) => sectionLabel(strOf(s))).filter((s) => s !== "");

  const isDoc =
    data !== null &&
    data !== undefined &&
    (markdown !== "" || kind !== "" || version !== "" || sections.length > 0);

  // compose_interview_guide：{类型: 提纲正文}；仅取非空字符串值（不臆造结构）
  const guides =
    !isDoc && data !== null && data !== undefined && !Array.isArray(data)
      ? Object.entries(rec)
          .filter((entry): entry is [string, string] => typeof entry[1] === "string")
          .map(([type, text]) => ({ type, text }))
          .filter((g) => g.text.trim() !== "")
      : [];

  const pid = projectId || strOf(rec.project_id) || strOf(rec.id);
  const title = isDoc ? KIND[kind] ?? (kind || "文档") : guides.length > 0 ? "访谈提纲" : "文档";
  const statusChip = isDoc
    ? version
      ? <Chip tone="neutral">{version}</Chip>
      : undefined
    : guides.length > 0
      ? <Chip tone="neutral">{`${guides.length} 类`}</Chip>
      : undefined;
  const subtitle = isDoc
    ? [`${sections.length} 章`, inputsHash ? `inputs ${inputsHash}` : ""].filter((s) => s !== "").join(" · ")
    : undefined;

  const empty = !isDoc && guides.length === 0;

  return (
    <CardShell
      title={title}
      subtitle={subtitle || undefined}
      icon={<IconBook className="w-4 h-4" />}
      degraded={degraded}
      statusChip={statusChip}
    >
      {empty ? (
        <NoData hint="未取得文档内容；可重试 compose_proposal / compose_interview_guide。" />
      ) : isDoc ? (
        <div className="space-y-3">
          {/* 体例：requested ≠ actual → 如实声明限制（不得粉饰为已按用户体例生成） */}
          {requestedMode && mode && requestedMode !== mode ? (
            <Notice tone="ochre">
              {`本次请求体例为 ${requestedMode}，实际生成体例为 ${mode}：该体例未获完整支持，正文头部已前置声明限制，投稿/开题前请人工补齐体例要求。`}
            </Notice>
          ) : mode ? (
            <div className="flex items-center gap-2 text-[11.5px] text-inkfaint">
              <span>生成体例</span>
              <Chip tone="indigo">{mode}</Chip>
            </div>
          ) : null}

          {/* 章节（八章导航同源，设计板 lines 680-690） */}
          {sections.length > 0 && (
            <div>
              <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-1.5">章节</div>
              <div className="flex flex-wrap gap-1.5">
                {sections.map((s, i) => (
                  <Chip key={`${s}-${i}`} tone="indigo">
                    {s}
                  </Chip>
                ))}
              </div>
            </div>
          )}

          <div className="text-[11px] text-inkfaint">{`正文 ${markdown.length} 字 · markdown 为唯一真源，卡内不改写`}</div>

          {/* 入口：查看（路由 /p/:id/proposal）+ 下载 .md（web_host/api/exports.py L27） */}
          {pid ? (
            <div className="flex flex-wrap items-center gap-2">
              <a
                href={`/p/${pid}/proposal`}
                className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-indigo text-[12.5px] hover:bg-indigowash transition"
              >
                查看
              </a>
              <a
                href={rest.proposalDownloadUrl(pid)}
                className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md hairline bg-card text-ink text-[12.5px] hover:bg-cardsunk transition"
              >
                <IconDownload className="w-3.5 h-3.5" />
                下载 .md
              </a>
            </div>
          ) : (
            <div className="text-[11px] text-inkfaint">未取得项目标识，暂不能生成查看与下载入口。</div>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {guides.map((g) => (
            <details key={g.type} className="hairline rounded-md bg-cardsunk/50">
              <summary className="px-3 py-2 text-[12.5px] text-inksoft flex items-center gap-1.5">
                <IconCaret className="w-3.5 h-3.5 caret" />
                {g.type}
              </summary>
              <div className="px-3 pb-3 font-serif text-[12.5px] leading-[1.85] text-inksoft whitespace-pre-wrap">
                {g.text}
              </div>
            </details>
          ))}
        </div>
      )}
    </CardShell>
  );
}
