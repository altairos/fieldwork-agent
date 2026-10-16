import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, apiDetail, rest, type ProposalMeta } from "../api/rest";
import { ChapterNav } from "../components/proposal/ChapterNav";
import { ProposalViewer } from "../components/proposal/ProposalViewer";
import { VersionSwitch } from "../components/proposal/VersionSwitch";
import { Header } from "../components/layout/Header";
import { Button } from "../components/ui/Button";
import { IconBook, IconDownload } from "../components/ui/icons";
import { authHeaders } from "../lib/keys";
import { useExportFormatStore } from "../stores/exportFormat";
import type { ProposalDoc } from "../types/models";

/**
 * ProposalView — 路由 `/p/:id/proposal`：八章提案阅读 + 版本/kind 切换 + 下载报告（md/docx/txt，格式随「我的」偏好）。
 * 设计板同源：view-proposal（lines 677-767）：
 *   左栏 240px（679-710）：题头「RESEARCH PROPOSAL」+ 项目名（682-686）、
 *     版本区（688-695 → VersionSwitch）、八章导航（696-705 → ChapterNav，滚动高亮）、
 *     底部「下载 .md」主按钮 + 「访谈提纲（kind 切换）」outline 按钮（706-709）
 *   右栏正文（712-767）：`overflow-y-auto scroll bg-paper/40` → ProposalViewer（max-w-720 纸面排版）
 * 移动端（<lg，批次 A）：网格回落单栏——左栏 auto 行限高 46vh（八章导航内部滚动，题头/版本/按钮
 *   常驻可见），正文 1fr；左栏分隔线改 border-b（lg+ 恢复设计板 border-r）。
 * 数据：GET /projects/{id}/proposal（最新文档正文）+ GET /projects/{id}/proposals（版本列表）；
 *   另取 GET /projects/{id} 用于 Header/题头的项目名与阶段（一次快照请求，只读展示）。
 * 数据源（审计 F13 修复）：选中版本 / 经 kind 切换选中条目后，改走 by-id 端点
 *   GET /projects/{id}/proposals/{doc_id} 加载对应正文；下载同理走 by-id 下载端点。
 * 确认入口（审计 F04）：POST /projects/{id}/proposal/confirm + proposal_confirmed /
 *   proposal_confirmation_stale 快照字段驱动「确认方案 / 重新确认方案」与失效提示。
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
  const [dlError, setDlError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [stale, setStale] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmHint, setConfirmHint] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  // 导出格式偏好（「我的」卡设置）：只读订阅，影响下载 URL 与按钮文案
  const format = useExportFormatStore((s) => s.format);
  const bodyRef = useRef<HTMLDivElement>(null);

  // 初载正文：最新文档（loadDoc 无参 = GET /projects/{id}/proposal）
  useEffect(() => {
    void loadDoc();
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    rest
      .listProposals(projectId)
      .then((list) => {
        if (alive) setVersions(list);
      })
      .catch(() => {
        // 版本列表失败不阻塞正文阅读：留空并由 doc 兜底派生单条
        if (alive) setVersions([]);
      });
    return () => {
      alive = false;
    };
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    rest
      .getProject(projectId)
      .then((snap) => {
        if (!alive) return;
        setProjectName(snap.research_context?.topic || "");
        setPhase(snap.phase);
        setConfirmed(!!snap.proposal_confirmed);
        setStale(!!snap.proposal_confirmation_stale);
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
  }, [doc, selectedId]);

  function jumpToChapter(i: number) {
    const root = bodyRef.current;
    if (!root) return;
    root.querySelectorAll("h2")[i]?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveIndex(i);
  }

  // 下载报告（md/docx/txt，格式随「我的」偏好）：/projects 前缀受 F02 令牌保护，顶层锚点导航带不了自定义头
  // （启用 App Token 必 401）→ fetch + authHeaders() 取 blob 走临时链下载。
  // F13：按当前选中文档的 id 走 by-id 下载端点（历史版本也能下载）。
  async function download() {
    if (!doc) return;
    setDlError(null);
    try {
      const resp = await fetch(rest.proposalByIdDownloadUrl(projectId, doc.id, format), {
        headers: authHeaders(),
      });
      if (!resp.ok) throw new ApiError(resp.status, "下载失败");
      const url = URL.createObjectURL(await resp.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: "" });
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setDlError(err instanceof ApiError ? `下载失败（HTTP ${err.status}）` : `下载失败：${String(err)}`);
    }
  }

  /** 加载文档正文：无参 = 最新（GET /proposal）；带 id = 指定版本/kind（by-id，F13）。 */
  async function loadDoc(docId?: string) {
    if (!projectId) return;
    try {
      const d = docId
        ? await rest.getProposalById(projectId, docId)
        : await rest.getProposal(projectId);
      setDoc(d);
      setSelectedId(d.id);
      setError(null);
    } catch (err: unknown) {
      setError(`提案加载失败：${String(err)}`);
    }
  }

  const otherKind = doc?.kind === "interview_guide" ? "proposal" : "interview_guide";

  // kind 切换（F13）：从版本列表选该 kind 最新一条，按 id 加载正文；
  // 列表为空（接口失败兜底）或该 kind 暂无文档时如实提示，不伪造内容。
  function toggleKind() {
    const pool = versions.length > 0 ? versions : versionList;
    const sameKind = pool.filter((v) => v.kind === otherKind);
    const target = sameKind[sameKind.length - 1];
    if (!target) {
      setError(`暂无${kindLabel(otherKind)}文档`);
      return;
    }
    void loadDoc(target.id);
  }

  // 确认方案（F04）：POST confirm → 回读快照刷新题头/阶段/确认与 stale 状态。
  // 阶段推进提示来自后端确定性推进结果（phase_transition.to）。
  async function doConfirm() {
    if (!projectId || !doc) return;
    setConfirming(true);
    setConfirmError(null);
    setConfirmHint(null);
    try {
      const res = await rest.confirmProposal(projectId, doc.id);
      const snap = await rest.getProject(projectId);
      setProjectName(snap.research_context?.topic || "");
      setPhase(snap.phase);
      setConfirmed(!!snap.proposal_confirmed);
      setStale(!!snap.proposal_confirmation_stale);
      const to = res.phase_transition?.to;
      setConfirmHint(to ? `方案已确认，阶段推进至 ${to}` : "方案已确认");
    } catch (err: unknown) {
      // R09：后端业务拒绝（400/404/409）的 {detail} 中文文案直显给用户
      if (err instanceof ApiError) {
        const detail = apiDetail(err);
        setConfirmError(
          err.status < 500 && detail ? detail : `确认失败（HTTP ${err.status}）`,
        );
      } else {
        setConfirmError(`确认失败：${String(err)}`);
      }
    } finally {
      setConfirming(false);
    }
  }

  // 版本列表失败/为空时用当前文档兜底派生单条（保证 VersionSwitch 有当前项高亮）
  const versionList: ProposalMeta[] =
    versions.length > 0
      ? versions
      : doc
        ? [{ id: doc.id, kind: doc.kind, version: doc.version }]
        : [];

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
      <div className="flex-1 grid grid-cols-1 grid-rows-[auto_1fr] lg:grid-cols-[240px_1fr] lg:grid-rows-1 min-h-0 hairline rounded-xl bg-card shadow-card overflow-hidden mx-4 mb-4">
        {/* 左栏：题头 + 版本 + 八章导航 + 下载/kind（设计板 681-710） */}
        <div className="bg-paperdeep border-b border-hairline lg:border-b-0 lg:border-r flex flex-col min-h-0 max-h-[46vh] overflow-y-auto scroll overscroll-contain lg:max-h-none lg:overflow-visible">
          <div className="px-4 py-3.5 border-b border-hairline">
            <div className="text-[10.5px] tracking-[.16em] text-inkfaint num">RESEARCH PROPOSAL</div>
            <div className="font-serif text-[14px] font-semibold leading-tight mt-0.5">
              {projectName || projectId}
            </div>
          </div>
          <div className="px-4 py-3 border-b border-hairline">
            <div className="text-[10.5px] text-inkfaint mb-2">版本</div>
            {versionList.length > 0 ? (
              <VersionSwitch versions={versionList} current={selectedId} onSelect={(vid) => void loadDoc(vid)} />
            ) : (
              <div className="text-[11.5px] text-inkfaint">暂无版本记录</div>
            )}
            {doc && error && (
              <div className="text-[11px] text-cinnabar mt-2 leading-snug">{error}</div>
            )}
          </div>
          <ChapterNav
            sections={doc ? doc.sections : []}
            activeIndex={activeIndex}
            onSelect={jumpToChapter}
          />
          <div className="p-3 border-t border-hairline space-y-2">
            <Button variant="primary" className="w-full" disabled={!doc} onClick={download}>
              <IconDownload className="w-4 h-4" />
              下载 .{format}
            </Button>
            {dlError && <div className="text-[11px] text-cinnabar leading-snug">{dlError}</div>}
            <button
              type="button"
              disabled={!doc}
              onClick={toggleKind}
              className="w-full h-9 rounded-md hairline bg-card text-[12.5px] text-inksoft flex items-center justify-center gap-1.5 hover:bg-cardsunk transition disabled:opacity-50 disabled:pointer-events-none"
            >
              <IconBook className="w-4 h-4" />
              {kindLabel(otherKind)}（kind 切换）
            </button>
            {confirmed && !stale && (
              <div className="text-[11px] text-bamboo leading-snug">方案已确认</div>
            )}
            {stale && (
              <div className="text-[11px] text-ochre leading-snug">
                研究信息已变更，此前的方案确认已失效，请重新确认
              </div>
            )}
            <Button
              variant="outline"
              className="w-full"
              disabled={!doc || confirming || doc.kind !== "proposal"}
              onClick={doConfirm}
            >
              {confirming ? "确认中…" : confirmed || stale ? "重新确认方案" : "确认方案"}
            </Button>
            {confirmHint && (
              <div className="text-[11px] text-bamboo leading-snug">{confirmHint}</div>
            )}
            {confirmError && (
              <div className="text-[11px] text-cinnabar leading-snug">{confirmError}</div>
            )}
          </div>
        </div>

        {/* 右栏正文（设计板 712-767） */}
        <div ref={bodyRef} className="overflow-y-auto scroll bg-paper/40 min-h-0">
          {doc ? (
            <ProposalViewer markdown={doc.markdown} kind={doc.kind} version={doc.version} />
          ) : error ? (
            <div className="max-w-[720px] mx-auto px-5 lg:px-10 py-10">
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
