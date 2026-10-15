import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { rest, type ProposalMeta } from "../api/rest";
import { ChapterNav } from "../components/proposal/ChapterNav";
import { ProposalViewer } from "../components/proposal/ProposalViewer";
import { VersionSwitch } from "../components/proposal/VersionSwitch";
import { Header } from "../components/layout/Header";
import { Button } from "../components/ui/Button";
import { IconBook, IconDownload } from "../components/ui/icons";
import type { ProposalDoc } from "../types/models";

/**
 * ProposalView — 路由 `/p/:id/proposal`：八章提案阅读 + 版本/kind 切换 + 下载 .md。
 * 设计板同源：view-proposal（lines 677-767）：
 *   左栏 240px（679-710）：题头「RESEARCH PROPOSAL」+ 项目名（682-686）、
 *     版本区（688-695 → VersionSwitch）、八章导航（696-705 → ChapterNav，滚动高亮）、
 *     底部「下载 .md」主按钮 + 「访谈提纲（kind 切换）」outline 按钮（706-709）
 *   右栏正文（712-767）：`overflow-y-auto scroll bg-paper/40` → ProposalViewer（max-w-720 纸面排版）
 * 数据：GET /projects/{id}/proposal（最新文档正文）+ GET /projects/{id}/proposals（版本列表）；
 *   另取 GET /projects/{id} 用于 Header/题头的项目名与阶段（一次快照请求，只读展示）。
 * API 限制（exports.py）：正文接口仅返回**最新保存**的文档，无法按 id/kind 拉取历史版本正文——
 *   选中非最新条目时如实提示限制并提供「回到最新版本」，不伪造内容（红线②同源精神）。
 */
type Doc = ProposalDoc & { id: string };

/** kind → 中文名（设计板 708「访谈提纲」） */
const KIND_LABEL: Record<string, string> = {
  proposal: "研究方案",
  interview_guide: "访谈提纲",
};
function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

export function ProposalView() {
  const { id } = useParams();
  const projectId = id ?? "";
  const navigate = useNavigate();
  const [doc, setDoc] = useState<Doc | null>(null);
  const [versions, setVersions] = useState<ProposalMeta[]>([]);
  const [projectName, setProjectName] = useState("");
  const [phase, setPhase] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedKind, setSelectedKind] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    rest
      .getProposal(projectId)
      .then((d) => {
        if (!alive) return;
        setDoc(d);
        setSelectedId(d.id);
        setSelectedKind(d.kind);
      })
      .catch((err: unknown) => {
        if (alive) setError(`提案加载失败：${String(err)}`);
      });
    rest
      .listProposals(projectId)
      .then((list) => {
        if (alive) setVersions(list);
      })
      .catch(() => {
        // 版本列表失败不阻塞正文阅读：留空并由 doc 兜底派生单条
        if (alive) setVersions([]);
      });
    rest
      .getProject(projectId)
      .then((snap) => {
        if (!alive) return;
        setProjectName(snap.research_context?.topic || "");
        setPhase(snap.phase);
      })
      .catch(() => {
        // 项目名仅用于题头展示，失败降级为项目 id（不伪造）
      });
    return () => {
      alive = false;
    };
  }, [projectId]);

  // 八章滚动高亮：观察正文内 h2（与后端 sections 的 `## ` 行同序），取最靠上的可见章节
  useEffect(() => {
    const root = bodyRef.current;
    if (!root || !doc || typeof IntersectionObserver === "undefined") return;
    const heads = Array.from(root.querySelectorAll("h2"));
    if (heads.length === 0) return;
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        const first = visible[0];
        if (first) {
          const idx = heads.indexOf(first.target as HTMLHeadingElement);
          if (idx >= 0) setActiveIndex(idx);
        }
      },
      { root, rootMargin: "-10% 0px -70% 0px", threshold: 0 },
    );
    heads.forEach((h) => io.observe(h));
    return () => io.disconnect();
  }, [doc, selectedId, selectedKind]);

  function jumpToChapter(i: number) {
    const root = bodyRef.current;
    if (!root) return;
    root.querySelectorAll("h2")[i]?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveIndex(i);
  }

  // 下载 .md：走 /projects/{id}/proposal/download（Content-Disposition attachment，锚点触发）
  function download() {
    const a = document.createElement("a");
    a.href = rest.proposalDownloadUrl(projectId);
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  const otherKind = doc?.kind === "interview_guide" ? "proposal" : "interview_guide";
  function toggleKind() {
    setSelectedKind((k) => (k === otherKind ? doc?.kind ?? null : otherKind));
  }
  function backToLatest() {
    if (!doc) return;
    setSelectedId(doc.id);
    setSelectedKind(doc.kind);
  }

  // 版本列表失败/为空时用当前文档兜底派生单条（保证 VersionSwitch 有当前项高亮）
  const versionList: ProposalMeta[] =
    versions.length > 0
      ? versions
      : doc
        ? [{ id: doc.id, kind: doc.kind, version: doc.version }]
        : [];
  // 选中条目与可读正文不一致（API 仅返回最新文档）→ 如实提示，不伪造内容
  const mismatch =
    doc !== null &&
    ((selectedId !== null && selectedId !== doc.id) ||
      (selectedKind !== null && selectedKind !== doc.kind));

  return (
    <div className="h-full flex flex-col">
      <Header
        projectName={projectName || projectId}
        phase={phase}
        view="proposal"
        onNavigate={(v) => {
          if (v === "workspace") navigate(`/p/${projectId}`);
        }}
        onExport={doc ? download : undefined}
      />
      <div className="flex-1 grid grid-cols-[240px_1fr] min-h-0 hairline rounded-xl bg-card shadow-card overflow-hidden mx-4 mb-4">
        {/* 左栏：题头 + 版本 + 八章导航 + 下载/kind（设计板 681-710） */}
        <div className="bg-paperdeep border-r border-hairline flex flex-col min-h-0">
          <div className="px-4 py-3.5 border-b border-hairline">
            <div className="text-[10.5px] tracking-[.16em] text-inkfaint num">RESEARCH PROPOSAL</div>
            <div className="font-serif text-[14px] font-semibold leading-tight mt-0.5">
              {projectName || projectId}
            </div>
          </div>
          <div className="px-4 py-3 border-b border-hairline">
            <div className="text-[10.5px] text-inkfaint mb-2">版本</div>
            {versionList.length > 0 ? (
              <VersionSwitch versions={versionList} current={selectedId} onSelect={setSelectedId} />
            ) : (
              <div className="text-[11.5px] text-inkfaint">暂无版本记录</div>
            )}
          </div>
          <ChapterNav
            sections={doc && !mismatch ? doc.sections : []}
            activeIndex={activeIndex}
            onSelect={jumpToChapter}
          />
          <div className="p-3 border-t border-hairline space-y-2">
            <Button variant="primary" className="w-full" disabled={!doc} onClick={download}>
              <IconDownload className="w-4 h-4" />
              下载 .md
            </Button>
            <button
              type="button"
              disabled={!doc}
              onClick={toggleKind}
              className="w-full h-9 rounded-md hairline bg-card text-[12.5px] text-inksoft flex items-center justify-center gap-1.5 hover:bg-cardsunk transition disabled:opacity-50 disabled:pointer-events-none"
            >
              <IconBook className="w-4 h-4" />
              {kindLabel(otherKind)}（kind 切换）
            </button>
          </div>
        </div>

        {/* 右栏正文（设计板 712-767） */}
        <div ref={bodyRef} className="overflow-y-auto scroll bg-paper/40 min-h-0">
          {doc && !mismatch ? (
            <ProposalViewer markdown={doc.markdown} kind={doc.kind} version={doc.version} />
          ) : mismatch && doc ? (
            <div className="max-w-[720px] mx-auto px-10 py-10">
              <div className="hairline rounded-lg bg-cardsunk p-5 text-[13px] text-inksoft leading-relaxed">
                所选条目（{kindLabel(selectedKind ?? doc.kind)}
                {selectedId && selectedId !== doc.id ? " · 历史版本" : ""}）的正文暂不可在线读取：
                当前接口 <code className="num text-indigo text-[12px]">GET /projects/{projectId}/proposal</code>{" "}
                仅返回最新保存的文档（{kindLabel(doc.kind)} · {doc.version}）。
                <div className="mt-3">
                  <Button variant="outline" size="sm" onClick={backToLatest}>
                    回到最新版本
                  </Button>
                </div>
              </div>
            </div>
          ) : error ? (
            <div className="max-w-[720px] mx-auto px-10 py-10">
              <div className="hairline rounded-lg bg-cinnabarwash/50 p-5 text-[13px] text-cinnabar leading-relaxed">
                {error}
              </div>
              <div className="mt-4 text-center">
                <Button variant="ghost" size="sm" onClick={() => navigate(`/p/${projectId}`)}>
                  返回工作台
                </Button>
              </div>
            </div>
          ) : (
            <div className="text-center text-inkfaint text-[13px] py-16">提案加载中…</div>
          )}
        </div>
      </div>
    </div>
  );
}
