import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ApiError, apiDetail, isAppTokenError, isSessionExpiredError, notifySessionExpiredIfAny, rest,
  type ProposalMeta,
} from "../api/rest";
import { AuthDialog } from "../components/auth/AuthDialog";
import { ChapterNav } from "../components/proposal/ChapterNav";
import { ExportFormatChips } from "../components/proposal/ExportFormatChips";
import { ProposalViewer } from "../components/proposal/ProposalViewer";
import { VersionSwitch } from "../components/proposal/VersionSwitch";
import { Header } from "../components/layout/Header";
import { Button } from "../components/ui/Button";
import { IconBook, IconDownload } from "../components/ui/icons";
import { authHeaders } from "../lib/keys";
import { getAuthEpoch, getSessionToken, isStaleSession, ownerHeaders } from "../lib/session";
import { useDisposeGuestProject } from "../hooks/useDisposeGuestProject";
import { useAuthStore } from "../stores/auth";
import { useExportFormatStore } from "../stores/exportFormat";
import type { ProposalDoc } from "../types/models";

/**
 * ProposalView — 路由 `/p/:id/proposal`：八章提案阅读 + 版本/kind 切换 + 下载报告（md/docx/txt，格式随「我的」偏好）。
 * 设计板同源：view-proposal（lines 677-767）：
 *   左栏 240px（679-710）：题头「RESEARCH PROPOSAL」+ 项目名（682-686）、
 *     版本区（688-695 → VersionSwitch）、八章导航（696-705 → ChapterNav，滚动高亮）、
 *     底部「下载 .md」主按钮 + 「查看访谈提纲/研究方案」kind 切换按钮（706-709）
 *   右栏正文（712-767）：`overflow-y-auto scroll bg-paper/40` → ProposalViewer（max-w-720 纸面排版）
 * 移动端（<lg，批次 A）：网格回落单栏——左栏 auto 行限高 46vh（八章导航内部滚动，题头/版本/按钮
 *   常驻可见），正文 1fr；左栏分隔线改 border-b（lg+ 恢复设计板 border-r）。
 * 数据：GET /projects/{id}/proposal（最新文档正文）+ GET /projects/{id}/proposals（版本列表）；
 *   另取 GET /projects/{id} 用于 Header/题头的项目名与阶段（一次快照请求，只读展示）。
 * 数据源（审计 F13 修复）：选中版本 / 经 kind 切换选中条目后，改走 by-id 端点
 *   GET /projects/{id}/proposals/{doc_id} 加载对应正文；下载同理走 by-id 下载端点。
 * 确认入口（审计 F04）：POST /projects/{id}/proposal/confirm + proposal_confirmed /
 *   proposal_confirmation_stale 快照字段驱动「确认方案 / 重新确认方案」与失效提示。
 * 身份作用域（评审 R03）：本页正文/版本/确认态是组件本地 state，不经过工作区四 store 的
 *   reset——登录身份变化（注销、会话失效清令牌、换号）时由下方作用域 effect 就地清空私有正文、
 *   作废在途请求，并亮出重新登录入口。
 * 请求代次（评审 R04）：正文加载用单调代次，只有最后一次选择的响应可落地——慢响应不能
 *   覆盖用户最后的选择（正文、高亮、下载与确认对象必须同源）。
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
  // 匿名离开清除记录：方案页是独立路由，工作台的监听此时已卸载，这条不能漏
  // （与 App 的 Workspace 共用同一 hook；已登录时 enabled=false 静默不生效）
  const viewer = useAuthStore((s) => s.user);
  useDisposeGuestProject(projectId, !viewer && !getSessionToken());
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
  const [authOpen, setAuthOpen] = useState(false);
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  // 导出格式偏好（「我的」卡设置）：只读订阅，影响下载 URL 与按钮文案
  const format = useExportFormatStore((s) => s.format);
  const bodyRef = useRef<HTMLDivElement>(null);
  // P1-5：默认文档自动纠正只做一次——用户主动切到访谈提纲后不得被再切回方案
  const autoProposalFixed = useRef(false);
  // 身份作用域代次（R03）：身份/项目变化即自增，旧作用域的异步结果一律弃置。
  const scopeRef = useRef(0);
  // 正文请求代次（R04）：每次选择/初载自增，只有最新代次的响应可以落地。
  const requestRef = useRef(0);
  const user = useAuthStore((s) => s.user);

  /** 认证类失败（会话失效 / 守卫令牌）：亮出重新登录入口并声明可判定性。
   *  会话失效时同步清空私有正文，不等 store 变更后的 effect——审计 R03 要求
   *  「会话清理与正文退出显示同步发生」。 */
  function noteAuthFailure(err: unknown): boolean {
    const expired = isSessionExpiredError(err);
    if (!expired && !isAppTokenError(err)) return false;
    setAuthNotice(expired
      ? "登录已失效，请重新登录后查看方案"
      : "访问令牌缺失或已失效，请在登录窗「高级 · API 密钥」中填写后重试");
    if (expired) {
      setDoc(null);
      setSelectedId(null);
      setConfirmed(false);
      setStale(false);
    }
    return true;
  }

  /** 加载文档正文：无参 = 最新（GET /proposal）；带 id = 指定版本/kind（by-id，F13）。
   *  R04：落地前校验作用域与请求代次——晚到的旧选择响应不得覆盖最后选择。 */
  async function loadDoc(docId?: string) {
    if (!projectId) return;
    const scope = scopeRef.current;
    const seq = ++requestRef.current;
    try {
      const d = docId
        ? await rest.getProposalById(projectId, docId)
        : await rest.getProposal(projectId);
      if (scope !== scopeRef.current || seq !== requestRef.current) return;
      setDoc(d);
      setSelectedId(d.id);
      setError(null);
      setAuthNotice(null);
    } catch (err: unknown) {
      // 认证类失败先处理再校验代次：401 的全局钩子（清会话→ user 变化）会让作用域自增，
      // 若先查作用域，刚发生的会话失效提示会被自己丢掉（R03 验收点）。
      // T03：迟到的旧身份 401 已在请求层标记 staleAuth——不得污染新作用域的提示。
      const authFailed = !(err instanceof ApiError && err.staleAuth)
        && noteAuthFailure(err);
      if (scope !== scopeRef.current || seq !== requestRef.current) return;
      if (authFailed) {
        setError(null);
        return;
      }
      // 用户友好文案（2026-10-18）：不透出响应体原文（如 {"detail":"尚无提案文档"}）；
      // 404 = 尚未生成 → 指引回工作台；其余按 HTTP 状态归类。
      if (err instanceof ApiError && err.status === 404) {
        setError("方案草稿尚未生成：回到工作台继续推进，生成后会自动出现在这里。");
      } else if (err instanceof ApiError) {
        setError(`方案草稿加载失败（HTTP ${err.status}），请稍后重试`);
      } else {
        setError("方案草稿加载失败，请稍后重试");
      }
    }
  }

  // 身份/项目作用域（R03）：先清空上一作用域的私有正文与派生态，再按当前身份重拉。
  // 文案、版本列表、项目题头与确认态全部在被清理范围内——页面无第二个残留通道。
  useEffect(() => {
    scopeRef.current += 1;
    requestRef.current += 1;
    autoProposalFixed.current = false;   // 新作用域重新执行一次默认文档纠正
    setDoc(null);
    setVersions([]);
    setSelectedId(null);
    setProjectName("");
    setPhase(undefined);
    setError(null);
    setDlError(null);
    setConfirmed(false);
    setStale(false);
    setConfirming(false);
    setConfirmHint(null);
    setConfirmError(null);
    // 会话失效提示只在匿名作用域内继续有效（等用户重登）；换了登录身份则立即清掉，
    // 避免刚登录就看到“登录已失效”。成功加载也会清它（loadDoc 内）。
    if (user) setAuthNotice(null);
    setActiveIndex(0);
    if (!projectId) return;
    const scope = scopeRef.current;
    void loadDoc();                        // 初载正文：最新文档（loadDoc 无参）
    rest
      .listProposals(projectId)
      .then((list) => {
        if (scope === scopeRef.current) setVersions(list);
      })
      .catch(() => {
        // 版本列表失败不阻塞正文阅读：留空并由 doc 兜底派生单条
        if (scope === scopeRef.current) setVersions([]);
      });
    rest
      .getProject(projectId)
      .then((snap) => {
        if (scope !== scopeRef.current) return;
        setProjectName(snap.research_context?.title || snap.research_context?.topic || "");
        setPhase(snap.phase);
        setConfirmed(!!snap.proposal_confirmed);
        setStale(!!snap.proposal_confirmation_stale);
      })
      .catch((err: unknown) => {
        // 项目名仅用于题头展示，失败降级为项目 id（不伪造）；认证失败另给重登入口
        if (scope === scopeRef.current) noteAuthFailure(err);
      });
    // loadDoc / noteAuthFailure 只读 props 与 setter，随作用域快照使用即可。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, user, reloadToken]);

  // P1-5：默认文档不能停在“最后写入的一份”——访谈提纲通常晚于研究方案落盘，
  // 旧实现进页签先看到提纲。初载拿到 latest 后，若它不是 proposal，就从版本
  // 列表切到最新一份 proposal（列表已并行拉取，通常在同一帧内完成）。
  // 只自动纠正一次：用户主动点「查看访谈提纲」后不再切回。
  useEffect(() => {
    if (autoProposalFixed.current || !doc || versions.length === 0) return;
    // 首个可判定的文档 + 版本列表：无论是否命中都视为“已自动纠正过”，
    // 此后用户主动切换 kind 不再被改回。
    autoProposalFixed.current = true;
    if (doc.kind === "proposal") return;
    const newestProposal = [...versions].reverse().find((v) => v.kind === "proposal");
    if (newestProposal && newestProposal.id !== doc.id) void loadDoc(newestProposal.id);
    // loadDoc 只读 props 与 setter；依赖保持最小以避免重复切换
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, versions]);

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
  // T02：失败响应不得丢弃——解析后复用 REST 同源的认证处理（全局会话清理 + 本页
  // 正文退出显示）；T03：按发起时的会话身份判定归属，旧身份 401 不清新会话。
  async function download() {
    if (!doc) return;
    const scope = scopeRef.current;
    const token = getSessionToken();
    const epoch = getAuthEpoch();
    setDlError(null);
    try {
      const resp = await fetch(rest.proposalByIdDownloadUrl(projectId, doc.id, format), {
        headers: { ...authHeaders(), ...ownerHeaders() },
      });
      if (!resp.ok) {
        const body = await resp.text().catch(() => resp.statusText);
        const err = new ApiError(resp.status, body, isStaleSession(token));
        notifySessionExpiredIfAny(err, epoch);
        // 旧身份的失败既不报当前页面，也不清理新会话
        if (err.staleAuth) return;
        // 当前身份的认证类失败：亮重登入口并同步退出私有正文（与确认路径同语义）
        if (noteAuthFailure(err)) return;
        throw err;
      }
      // 读取 blob 的 await 窗口内换号/退出 → 旧下载不得交付
      if (scope !== scopeRef.current || epoch !== getAuthEpoch()) return;
      const blob = await resp.blob();
      if (scope !== scopeRef.current || epoch !== getAuthEpoch()) return;
      const url = URL.createObjectURL(blob);
      const a = Object.assign(document.createElement("a"), { href: url, download: "" });
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      if (scope !== scopeRef.current) return;
      setDlError(err instanceof ApiError ? `下载失败（HTTP ${err.status}）` : `下载失败：${String(err)}`);
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
  // R03：身份作用域已变时不再回写任何状态（旧账号的确认结果不上新页面）。
  async function doConfirm() {
    if (!projectId || !doc) return;
    const scope = scopeRef.current;
    setConfirming(true);
    setConfirmError(null);
    setConfirmHint(null);
    try {
      const res = await rest.confirmProposal(projectId, doc.id);
      const snap = await rest.getProject(projectId);
      if (scope !== scopeRef.current) return;
      setProjectName(snap.research_context?.title || snap.research_context?.topic || "");
      setPhase(snap.phase);
      setConfirmed(!!snap.proposal_confirmed);
      setStale(!!snap.proposal_confirmation_stale);
      const to = res.phase_transition?.to;
      setConfirmHint(to ? `方案已确认，阶段推进至 ${to}` : "方案已确认");
    } catch (err: unknown) {
      // 同 loadDoc：认证失效优先于作用域校验（会话失效会同步推高作用域代次）；
      // T03：旧身份迟到的 401（staleAuth）不按当前身份处理
      const authFailed = !(err instanceof ApiError && err.staleAuth)
        && noteAuthFailure(err);
      if (authFailed) {
        if (scope === scopeRef.current) setConfirmError("登录已失效，请重新登录后确认方案");
        return;
      }
      if (scope !== scopeRef.current) return;
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
      if (scope === scopeRef.current) setConfirming(false);
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
        projectName={projectName || "未命名项目"}
        phase={phase}
        view="proposal"
        onOpenAuth={() => setAuthOpen(true)}
        onRename={(name) => {
          // 任务1：改名 → PUT title → 重拉快照回读（题头 projectName 本地 state 同步）
          void (async () => {
            try {
              await rest.updateResearchContext(projectId, { title: name });
              const snap = await rest.getProject(projectId);
              setProjectName(snap.research_context?.title || snap.research_context?.topic || "");
            } catch (e) {
              // 改名失败保持原名（无全局 toast 通道；用户重试即可）
              console.warn("rename failed:", e);
            }
          })();
        }}
        onNavigate={(v) => {
          if (v === "workspace") navigate(`/p/${projectId}`);
        }}
      />
      {/* 认证失效横幅（R03）：正文已随会话清除，这里给可达的重新登录入口 */}
      {authNotice && (
        <div
          role="alert"
          className="mx-4 mb-2 hairline rounded-lg bg-cinnabarwash/50 px-4 py-2.5 text-[12.5px] text-cinnabar flex items-center justify-between gap-3"
        >
          <span className="leading-snug">{authNotice}</span>
          <Button variant="outline" size="sm" onClick={() => setAuthOpen(true)}>
            重新登录
          </Button>
        </div>
      )}
      <div className="flex-1 grid grid-cols-1 grid-rows-[auto_1fr] lg:grid-cols-[240px_1fr] lg:grid-rows-1 min-h-0 hairline rounded-xl bg-card shadow-card overflow-hidden mx-4 mb-4">
        {/* 左栏：题头 + 版本 + 八章导航 + 下载/kind（设计板 681-710） */}
        <div className="bg-paperdeep border-b border-hairline lg:border-b-0 lg:border-r flex flex-col min-h-0 max-h-[46vh] overflow-y-auto scroll overscroll-contain lg:max-h-none lg:overflow-visible">
          <div className="px-4 py-3.5 border-b border-hairline">
            <div className="text-[10.5px] tracking-[.16em] text-inkfaint num">RESEARCH PROPOSAL</div>
            <div className="font-serif text-[14px] font-semibold leading-tight mt-0.5">
              {projectName || "未命名项目"}
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
            {/* 导出格式选择（首页「我的」卡解体后迁此：chips 即改即生效于下载 URL/文案） */}
            <ExportFormatChips />
            <Button variant="primary" className="w-full" disabled={!doc} onClick={download}>
              <IconDownload className="w-4 h-4" />
              下载 .{format}
            </Button>
            {dlError && <div className="text-[11px] text-cinnabar leading-snug">{dlError}</div>}
            <button
              type="button"
              data-testid="kind-switch"
              disabled={!doc}
              onClick={toggleKind}
              className="w-full h-9 rounded-md hairline bg-card text-[12.5px] text-inksoft flex items-center justify-center gap-1.5 hover:bg-cardsunk transition disabled:opacity-50 disabled:pointer-events-none cursor-pointer"
            >
              <IconBook className="w-4 h-4" />
              查看{kindLabel(otherKind)}
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

        {/* 右栏正文（设计板 712-767）。优先级：正文 → 认证失效（R03：会话没了就不能再展示
            私有正文，也不能被随后的 404 文案掩盖）→ 加载错误 → 加载中 */}
        <div ref={bodyRef} className="overflow-y-auto scroll bg-paper/40 min-h-0">
          {doc ? (
            <ProposalViewer markdown={doc.markdown} kind={doc.kind} version={doc.version} />
          ) : authNotice ? (
            <div className="max-w-[720px] mx-auto px-5 lg:px-10 py-10">
              <div className="hairline rounded-lg bg-paperdeep p-5 text-[13px] text-inksoft leading-relaxed">
                {authNotice}
              </div>
              <div className="mt-4 flex items-center justify-center gap-2">
                <Button size="sm" onClick={() => setAuthOpen(true)}>重新登录</Button>
                <Button variant="ghost" size="sm" onClick={() => navigate(`/p/${projectId}`)}>
                  返回工作台
                </Button>
              </div>
            </div>
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
            <div className="text-center text-inkfaint text-[13px] py-16">方案草稿加载中…</div>
          )}
        </div>
      </div>
      {/* 登录/注册弹窗（R03）：关窗即重拉——窗内可能已重新登录或补填访问令牌 */}
      <AuthDialog
        open={authOpen}
        onClose={() => {
          setAuthOpen(false);
          setReloadToken((n) => n + 1);
        }}
      />
    </div>
  );
}
