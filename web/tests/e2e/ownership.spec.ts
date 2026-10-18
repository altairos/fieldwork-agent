import { test, expect, type BrowserContext } from "@playwright/test";

/**
 * 跨浏览器上下文归属隔离 e2e — 跑在归属化 mock（Task 10）+ owner 注入前端（Task 9）：
 *   ctxA（默认 context，localStorage 已存 owner_token A）UI 创建项目 →
 *   ctxB（browser.newContext()，空 localStorage → 懒生成不同的 owner_token B）
 *   goto 同一项目 URL：GET /projects/:id 404（owner 不符）→
 *     App.tsx getProject catch → error(snapshot_failed) → degradedBanner
 *     「项目快照加载失败」如实呈现，且快照数据（「大理乡村旅居研究」）不泄露；
 *   ctxA 同 owner 不受影响：续聊 → mock 回放 → 住宿卡条目可见。
 * workers:1 全局串行（playwright.config），不与 keys.spec 的 settingsState 翻转并发。
 * 注：「大理乡村旅居研究」可见断言 scope 到 banner —— getByText 会同时命中右栏
 *     研究主题 textarea 的同值 value（Task 10 keys.spec 修复同款消歧）。
 */
test("跨上下文隔离：他人 URL 404 降级横幅，原 context 畅通", async ({ page, browser }) => {
  // ① ctxA：UI 创建项目（textarea + 「创建项目」→ POST /projects 带令牌 A → mock 登记 owner）
  await page.goto("/");
  await page.locator("textarea").first().fill("研究大理乡村旅居，单人两周，预算一万元");
  await page.getByRole("button", { name: "创建项目" }).click();
  await expect(page).toHaveURL(/\/p\/[^/]+/, { timeout: 10_000 });
  const projectUrl = page.url();
  // 创建后快照加载（归属相符 → fakeSnapshot；Header 项目名来自 research_context.topic）
  await expect(page.getByRole("banner").getByText("大理乡村旅居研究")).toBeVisible({ timeout: 10_000 });

  // ② ctxB：全新 context（空 localStorage → 懒生成不同令牌 B）访问同一 URL
  const ctxB: BrowserContext = await browser.newContext();
  const pageB = await ctxB.newPage();
  try {
    // ctxB 首页列表为空：GET /projects 按令牌过滤 → 不含 ctxA 项目
    //（Hub 任何区域不出现该项目名；与后端/mock 层「无令牌/他人令牌列表 []」断言互补）
    await pageB.goto("/");
    await expect(pageB.getByText("大理乡村旅居研究")).toBeHidden();
    await pageB.goto(projectUrl);
    // owner 不符 → 404 → App.tsx getProject catch → degradedBanner（PhaseBanner 如实转述）。
    // ui store 横幅为单槽位（degradedBanner 覆盖式）：getProject 归属 404 与 getMessages
    // 路由 404 并发，后到者胜出 → 「项目快照加载失败」/「对话历史加载失败」哪条呈现取决于
    // 完成顺序，断言放宽为任一（两者都证明 404 如实上屏、不伪造内容）
    await expect(pageB.getByText(/加载失败/)).toBeVisible({ timeout: 10_000 });
    // 快照确未加载：Header 持「未命名项目」占位（归属相符时此处是 topic 项目名）
    await expect(pageB.getByRole("banner").getByText("未命名项目")).toBeVisible();
    // 隔离不泄露存在性：他人快照数据不得渲染
    await expect(pageB.getByText("大理乡村旅居研究")).toBeHidden();
  } finally {
    await ctxB.close(); // 手动 newContext 须手动 close，不阻塞 workers:1 的后续 spec
  }

  // ③ ctxA 不受影响：同 owner 畅通（续聊 → 两步式回合回放 → 住宿卡条目）
  await page.getByLabel("消息输入").fill("帮我搜一下大理的住宿");
  await page.getByLabel("消息输入").press("Enter");
  await expect(page.getByText("大理古城客栈").first()).toBeVisible({ timeout: 15_000 });
});
