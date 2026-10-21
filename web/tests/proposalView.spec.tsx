import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProposalView } from "../src/routes/ProposalView";
import { useAuthStore } from "../src/stores/auth";
import {
  clearSessionToken, getSessionToken, setSessionToken,
} from "../src/lib/session";

const LATEST = {
  id: "prop_2",
  kind: "proposal",
  version: "final",
  created_at: "2026-10-16T10:00:00",
  markdown: "# 最新版正文\n\n段落。",
  // 真实后端 _proposal_payload 的 sections 仅从 `## ` 行派生；h1-only 正文 → []
  sections: [],
};
const V1 = {
  id: "prop_1",
  kind: "proposal",
  version: "draft",
  created_at: "2026-10-15T10:00:00",
  markdown: "# 初版正文\n\n段落。",
  sections: [],
};
const GUIDE = {
  id: "guide_1",
  kind: "interview_guide",
  version: "draft",
  created_at: "2026-10-16T11:00:00",
  markdown: "# 提纲正文\n\n段落。",
  sections: [],
};
const VERSIONS = [V1, LATEST, GUIDE];
const SNAP = {
  id: "p1",
  phase: "proposal_generation",
  research_context: { topic: "宗族变迁" },
  field_sites: [],
  informants: [],
};

function json(payload: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => String(payload),
  } as Response;
}

/** 按 URL 分发的 fetch stub；返回调用记录（url + init）。 */
function stubFetch(snap: object = SNAP) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/projects/p1") return json(snap);
      if (url === "/projects/p1/proposals") return json(VERSIONS);
      if (url === "/projects/p1/proposals/prop_1") return json(V1);
      if (url === "/projects/p1/proposals/guide_1") return json(GUIDE);
      if (url === "/projects/p1/proposal") return json(LATEST);
      if (url === "/projects/p1/proposal/confirm") {
        return json({
          confirmation: {},
          proposal_confirmed: true,
          phase_transition: { from: "proposal_generation", to: "finished" },
        });
      }
      return json({});
    }),
  );
  return calls;
}

/** stubFetch 变体：confirm 端点返回 4xx + {detail}（R09 拒绝路径），其余端点照常。 */
function stubFetchWithConfirmFailure(failure: { status: number; detail: string }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, _init?: RequestInit) => {
      if (url === "/projects/p1/proposal/confirm") {
        return {
          ok: false,
          status: failure.status,
          text: async () => JSON.stringify({ detail: failure.detail }),
        } as Response;
      }
      if (url === "/projects/p1") return json(SNAP);
      if (url === "/projects/p1/proposals") return json(VERSIONS);
      if (url === "/projects/p1/proposal") return json(LATEST);
      return json({});
    }),
  );
}

function mount() {
  return render(
    <MemoryRouter initialEntries={["/p/p1/proposal"]}>
      <Routes>
        <Route path="/p/:id/proposal" element={<ProposalView />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

beforeEach(() => {
  // 身份作用域用例的前置：默认匿名，仅登录态用例自行置位
  useAuthStore.setState({ user: null, expired: false });
  try { localStorage.clear(); } catch { /* jsdom 无 localStorage 时忽略 */ }
});

describe("ProposalView 版本/kind 加载与方案确认", () => {
  it("初载渲染最新版正文与 final chip（回归保护，现状即绿）", async () => {
    const calls = stubFetch();
    mount();
    expect(await screen.findByText("最新版正文")).toBeTruthy();
    expect(screen.getByText("final")).toBeTruthy();
    const urls = calls.map((c) => c.url);
    expect(urls).toContain("/projects/p1/proposal");
    expect(urls).toContain("/projects/p1/proposals");
  });

  it("点版本 chip 后按 id 加载对应正文（F13）", async () => {
    const calls = stubFetch();
    mount();
    await screen.findByText("最新版正文");
    // VERSIONS 顺序 [V1, LATEST, GUIDE]：DOM 中第一枚「draft v1」即 prop_1
    fireEvent.click(screen.getAllByText("draft v1")[0]);
    expect(await screen.findByText("初版正文")).toBeTruthy();
    expect(calls.some((c) => c.url === "/projects/p1/proposals/prop_1")).toBe(true);
  });

  it("点 kind 切换按钮加载访谈提纲正文（F13）", async () => {
    stubFetch();
    mount();
    await screen.findByText("最新版正文");
    fireEvent.click(screen.getByRole("button", { name: /访谈提纲/ }));
    expect(await screen.findByText("提纲正文")).toBeTruthy();
  });

  it("点确认方案——POST confirm 携 doc_id、回读快照、提示阶段推进（F04/R09）", async () => {
    const calls = stubFetch();
    mount();
    await screen.findByText("最新版正文");
    fireEvent.click(screen.getByRole("button", { name: "确认方案" }));
    await waitFor(() => screen.getByText(/阶段推进至 finished/));
    const confirms = calls.filter((c) => c.url === "/projects/p1/proposal/confirm");
    expect(confirms).toHaveLength(1);
    expect((confirms[0].init as RequestInit | undefined)?.method).toBe("POST");
    expect(JSON.parse(String((confirms[0].init as RequestInit).body)))
      .toEqual({ doc_id: "prop_2" });
    // confirm 后需回读项目快照刷新题头/阶段/确认状态
    expect(calls.filter((c) => c.url === "/projects/p1").length).toBeGreaterThanOrEqual(2);
  });

  it("stale 快照——提示重新确认且按钮文案变化（F04）", async () => {
    stubFetch({ ...SNAP, proposal_confirmed: true, proposal_confirmation_stale: true });
    mount();
    expect(await screen.findByText(/请重新确认/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新确认方案" })).toBeTruthy();
  });

  it("kind 切换到访谈提纲后确认按钮禁用（R09：只能确认 proposal）", async () => {
    stubFetch();
    mount();
    await screen.findByText("最新版正文");
    // 「访谈提纲（kind 切换）」按钮 → toggleKind 从版本列表取 guide_1（by-id 加载）
    fireEvent.click(screen.getByRole("button", { name: /访谈提纲/ }));
    expect(await screen.findByText("提纲正文")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认方案" })).toBeDisabled();
  });

  it("确认被拒（409）——直显后端 detail 文案（R09）", async () => {
    stubFetchWithConfirmFailure({
      status: 409,
      detail: "研究输入已变化，该文档的确认已过期：请重新生成方案",
    });
    mount();
    await screen.findByText("最新版正文");
    fireEvent.click(screen.getByRole("button", { name: "确认方案" }));
    expect(await screen.findByText(/请重新生成方案/)).toBeTruthy();
  });

  // ── 任务1：项目显示链 title || topic || 未命名项目 + Header 就地改名 ──
  it("题头显示链 title 优先 + Header 改名走 PUT /research-context（任务1）", async () => {
    const calls = stubFetch({
      ...SNAP,
      research_context: { topic: "宗族变迁", title: "宗族调研手记" },
    });
    mount();
    // title 优先于 topic：Header（可改名 button）与左栏题头两处同源
    // （projectName 来自异步 getProject，初载先显「未命名项目」，故用 findBy 等待）
    expect(await screen.findByRole("button", { name: "宗族调研手记" })).toBeTruthy();
    expect(screen.getAllByText("宗族调研手记").length).toBe(2);
    // 就地改名：点击 → 编辑 → Enter 提交（PUT title）→ 重拉快照回读
    fireEvent.click(screen.getByRole("button", { name: "宗族调研手记" }));
    const input = screen.getByLabelText("项目名称");
    fireEvent.change(input, { target: { value: "宗族组织调研" } });
    fireEvent.keyDown(input, { key: "Enter" });
    const puts = calls.filter((c) => c.url === "/projects/p1/research-context");
    expect(puts).toHaveLength(1);
    expect((puts[0].init as RequestInit).method).toBe("PUT");
    expect(JSON.parse(String((puts[0].init as RequestInit).body)))
      .toEqual({ title: "宗族组织调研" });
    // 改名后重拉快照（stub 静态返回旧 title，只验证回读调用发生）
    await waitFor(() =>
      expect(calls.filter((c) => c.url === "/projects/p1").length).toBeGreaterThanOrEqual(2));
  });

  it("无 title 且无 topic：题头兑底「未命名项目」，projectId 永不作显示名（任务1）", async () => {
    stubFetch({ ...SNAP, research_context: {} });
    mount();
    // Header 与左栏题头两处兑底；字母+数字的 project_id 不面向用户
    expect(await screen.findAllByText("未命名项目")).toBeTruthy();
    expect(screen.getAllByText("未命名项目").length).toBe(2);
    expect(screen.queryByText(/p1/)).toBeNull();
  });
});

describe("ProposalView 身份作用域与请求代次（R03/R04）", () => {
  const ALICE = { user_id: "u-alice", email: "alice@example.test" };

  /** 会话失效后匿名已无法读该项目（真实后端 404）；确认端点返回 401 session_expired。 */
  function stubExpiredSession() {
    let revoked = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/projects/p1/proposal/confirm") {
          revoked = true;
          return {
            ok: false,
            status: 401,
            text: async () =>
              JSON.stringify({ detail: { code: "session_expired", message: "登录会话已失效" } }),
          } as Response;
        }
        if (url === "/projects/p1/proposals") return json(VERSIONS);
        if (url === "/projects/p1/proposal") {
          // 会话被撤销后，匿名身份不再拥有该项目 → 404
          return revoked
            ? ({ ok: false, status: 404, text: async () => JSON.stringify({ detail: "项目不存在" }) } as Response)
            : json(LATEST);
        }
        if (url === "/projects/p1") return json(SNAP);
        return json({});
      }),
    );
  }

  it("R03：确认遇 401 session_expired → 会话清理与私有正文退出同步发生，并给重登入口", async () => {
    useAuthStore.setState({ user: ALICE });
    stubExpiredSession();
    mount();
    expect(await screen.findByText("最新版正文")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "确认方案" }));

    // 正文立即退出显示（审计 R03：privateDocVisible=false）
    await waitFor(() => expect(screen.queryByText("最新版正文")).toBeNull());
    // 全局会话失效钩子已清登录态
    expect(useAuthStore.getState().user).toBeNull();
    // 重登入口可达（横幅与正文区各一个「重新登录」，Header 回到「登录」）
    expect(screen.getAllByRole("button", { name: "重新登录" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "登录" }).length).toBeGreaterThanOrEqual(1);
    // 旧正文不得以任何形式留在文档里（privateDocVisible=false）
    expect(document.body.textContent).not.toContain("最新版正文");
    expect(document.body.textContent).not.toContain("段落");
  });

  it("R04：先点 v1 后点 v2，v1 慢响应不得覆盖最后选择（正文/高亮同源）", async () => {
    const D1 = { ...V1, markdown: "# 初版正文\n\n段落。" };
    const D2 = { ...LATEST, id: "prop_3", version: "draft", markdown: "# 第二版正文\n\n段落。" };
    let releaseV1!: (r: Response) => void;
    const v1Pending = new Promise<Response>((resolve) => { releaseV1 = resolve; });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/projects/p1/proposals/prop_1") return v1Pending;           // v1：挂起
        if (url === "/projects/p1/proposals/prop_3") return json(D2);            // v2：立即返回
        if (url === "/projects/p1/proposals") {
          return json([{ ...D1, created_at: "2026-10-15T10:00:00" },
                       { ...D2, created_at: "2026-10-16T10:00:00" }]);
        }
        if (url === "/projects/p1/proposal") return json(LATEST);
        if (url === "/projects/p1") return json(SNAP);
        return json({});
      }),
    );
    mount();
    expect(await screen.findByText("最新版正文")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "draft v1" }));
    fireEvent.click(screen.getByRole("button", { name: "draft v2" }));
    expect(await screen.findByText("第二版正文")).toBeTruthy();

    // v1 的慢响应此刻才到：不得覆盖 v2
    releaseV1(json({ ...D1, id: "prop_1" }) as Response);
    await waitFor(() => expect(screen.getByRole("button", { name: "draft v2" }))
      .toHaveAttribute("aria-pressed", "true"));
    expect(screen.queryByText("初版正文")).toBeNull();
    expect(screen.getByRole("button", { name: "draft v1" }))
      .toHaveAttribute("aria-pressed", "false");
  });
});

// 下载入口的认证处理（评审 T02/T03）：与确认入口同一套会话失效语义——
// 解析失败响应体、命中 session_expired 时清凭据并退出正文；旧身份迟到的 401 不清新会话。
describe("ProposalView 下载入口认证处理（T02/T03）", () => {
  const ALICE = { user_id: "u-alice", email: "alice@example.test" };
  const BOB = { user_id: "u-bob", email: "bob@example.test" };
  const DL = "/projects/p1/proposals/prop_2/download";

  /** 下载端点返回指定失败；401 会话失效后 GET 正文回 404（真实后端匿名不再拥有项目）。 */
  function stubDownload(failure: { status: number; body: string }) {
    let revoked = false;
    const fetchMock = vi.fn(async (url: string) => {
      if (url === DL) {
        revoked = failure.status === 401;
        return { ok: false, status: failure.status, text: async () => failure.body } as Response;
      }
      if (url === "/projects/p1/proposals") return json(VERSIONS);
      if (url === "/projects/p1/proposal") {
        return revoked
          ? ({ ok: false, status: 404, text: async () => JSON.stringify({ detail: "项目不存在" }) } as Response)
          : json(LATEST);
      }
      if (url === "/projects/p1") return json(SNAP);
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  function loginAs(user: { user_id: string; email: string }, token: string) {
    setSessionToken(token);
    useAuthStore.setState({ user, expired: false });
  }

  it("T02：下载遇 401 session_expired → 清凭据、正文退出显示、给重登入口", async () => {
    loginAs(ALICE, "sess_alice");
    stubDownload({ status: 401, body: JSON.stringify({ detail: { code: "session_expired" } }) });
    mount();
    expect(await screen.findByText("最新版正文")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /下载/ }));

    await waitFor(() => expect(screen.queryByText("最新版正文")).toBeNull());
    expect(getSessionToken()).toBe("");
    expect(useAuthStore.getState().expired).toBe(true);
    expect(screen.getAllByRole("button", { name: "重新登录" }).length).toBeGreaterThanOrEqual(1);
    expect(document.body.textContent).not.toContain("最新版正文");
  });

  it("T02：普通下载错误（500）不误报会话失效，正文保留", async () => {
    loginAs(ALICE, "sess_alice");
    stubDownload({ status: 500, body: "Internal Error" });
    mount();
    expect(await screen.findByText("最新版正文")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /下载/ }));

    expect(await screen.findByText(/下载失败（HTTP 500）/)).toBeTruthy();
    expect(screen.getByText("最新版正文")).toBeTruthy();
    expect(getSessionToken()).toBe("sess_alice");
    expect(useAuthStore.getState().user).toEqual(ALICE);
    expect(useAuthStore.getState().expired).toBe(false);
  });

  it("T03：旧身份迟到的下载 401 不清新会话，也不给新身份亮失效提示", async () => {
    loginAs(ALICE, "sess_alice");
    let release!: (r: Response) => void;
    const pending = new Promise<Response>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === DL) return pending;                       // 旧身份的下载：挂起
      if (url === "/projects/p1/proposals") return json(VERSIONS);
      if (url === "/projects/p1/proposal") return json(LATEST);
      if (url === "/projects/p1") return json(SNAP);
      return json({});
    }));
    mount();
    expect(await screen.findByText("最新版正文")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /下载/ }));

    // 下载未落地前换号：Bob 已取得有效会话，Alice 的旧 401 此刻才到
    act(() => {
      clearSessionToken();
      loginAs(BOB, "sess_bob");
    });
    release({
      ok: false, status: 401,
      text: async () => JSON.stringify({ detail: { code: "session_expired" } }),
    } as Response);

    await waitFor(() => expect(screen.getByText("最新版正文")).toBeTruthy());
    expect(getSessionToken()).toBe("sess_bob");
    expect(useAuthStore.getState().user).toEqual(BOB);
    expect(useAuthStore.getState().expired).toBe(false);
    expect(screen.queryByText(/登录已失效/)).toBeNull();
  });
});

describe("ProposalView 降级稿提示（P3-2）", () => {
  it("文档带 degraded 时在正文顶部显示降级原因", async () => {
    const degraded = { ...LATEST, degraded: "模板降级版（未接入 LLM）" };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/projects/p1") return json(SNAP);
      if (url === "/projects/p1/proposals") return json([degraded]);
      if (url === "/projects/p1/proposal") return json(degraded);
      return json({});
    }));
    mount();
    const banner = await screen.findByTestId("proposal-degraded");
    expect(banner.textContent).toContain("模板降级版（未接入 LLM）");
  });

  it("无 degraded 时不渲染提示条", async () => {
    stubFetch();
    mount();
    expect(await screen.findByText("最新版正文")).toBeTruthy();
    expect(screen.queryByTestId("proposal-degraded")).toBeNull();
  });
});
