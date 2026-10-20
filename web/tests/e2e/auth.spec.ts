import { test, expect, type BrowserContext } from "@playwright/test";

/**
 * 登录认领与跨用户隔离 e2e — 跑在 mock /auth 四端点（Task 15）+ 前端登录 UI
 * （Task 15）+ owner 注入（Task 9）的三段剧本：
 *   ① ctxA 匿名建项目（懒生成 anon_token → mock 登记 owner anon:<token>）
 *   ② 同 ctx 注册 alice（auth.register 体带 anon_token → mock 认领：owner 切
 *      user:<alice>）→ 回首页展开「我的项目」折叠列表，被认领项目仍可见
 *     （sess_ 令牌归属解析，GET /projects 按 user:<alice> 过滤命中）
 *   ③ ctxB 全新 context（空 localStorage → 懒生成不同 anon_token）注册 bob
 *      → 认领不到 alice 的项目 → bob 的 GET /projects 响应体空数组 + 折叠
 *      入口不渲染（owner 过滤隔离的双断言，fix round 1）
 *
 * 选择器消歧（Playwright getByRole name 默认子串匹配）：首页/顶栏「登录」入口钮
 *   与 AuthDialog 提交钮（login 模式）同名、「没有账号？注册」含「注册」子串——
 *   入口/提交一律 exact，模式切换用 /没有账号/ 定位；dialog 打开前页面只有一个
 *   「登录」钮，入口点击用 exact 即安全。
 * mock 的 research_context.topic 恒为「大理乡村旅居研究」（与输入文本无关）——
 *   alice 段断言用子串「大理乡村旅居」；Hub 项目列表默认折叠（重设计 Ruling
 *   P-1/P-2），非空列表须先点「继续先前项目」展开、scope 到列表行按钮断言（^ 锚定
 *   排除行尾删除钮同子串 aria-label）；bob 段不展开也不查 DOM 文本，改走响应体
 *   + 折叠入口 count 0（getByText toBeHidden 在默认折叠下恒过，守卫不了泄漏）。
 * workers:1 全局串行（playwright.config），不与 keys.spec 的 settingsState 翻转并发。
 */
test("匿名建项 → 注册认领 → 跨上下文隔离", async ({ page, browser }) => {
  // 三段剧本实测 25~31s（mock/dev server 冷启动波动），全局 30s 裕度窄会偶发红：
  // 单测级放宽到 60s（config 的 timeout: 30_000 全局默认不动）
  test.setTimeout(60_000);
  // ① ctxA 匿名建项目（textarea 一句话 → POST /projects 带懒生成 anon_token → mock 登记 owner）
  await page.goto("/");
  await page.locator("textarea").first().fill("研究大理乡村旅居");
  await page.getByRole("button", { name: "创建项目" }).click();
  await expect(page).toHaveURL(/\/p\/[^/]+/, { timeout: 10_000 });

  // ② 对话页顶栏「登录」入口（App 级 AuthDialog）→ 切注册模式 → alice 注册：
  //    注册体带①中同一 anon_token → mock 认领 owner 切 user:<alice>；
  //    默认 mode 是 login，须先点切换钮「没有账号？注册」再提交
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: /没有账号/ }).click();
  await page.getByLabel("邮箱").fill("alice@example.com");
  // exact：显隐钮 aria-label「显示密码/隐藏密码」与密码框构成子串关系（AuthDialog 改版）
  await page.getByLabel("密码", { exact: true }).fill("s3cret-pass");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden({ timeout: 10_000 });
  await page.goto("/");
  // 认领后项目仍可见：列表默认收起，先展开折叠入口；断言 scope 到列表行按钮
  // （accessible name 以 topic「大理乡村旅居研究」开头——^ 锚定排除行尾删除钮
  //   「删除项目 大理乡村旅居研究」的同子串 aria-label，参 ownership.spec 消歧做法）
  await page.getByRole("button", { name: /继续先前项目/ }).click();
  await expect(page.getByRole("button", { name: /^大理乡村旅居/ })).toBeVisible({ timeout: 10_000 });

  // ③ ctxB 另一用户看不到 alice 的项目
  const ctxB: BrowserContext = await browser.newContext();
  const pageB = await ctxB.newPage();
  try {
    await pageB.goto("/");
    await pageB.getByRole("button", { name: "登录", exact: true }).click();
    await pageB.getByRole("button", { name: /没有账号/ }).click();
    await pageB.getByLabel("邮箱").fill("bob@example.com");
    await pageB.getByLabel("密码", { exact: true }).fill("another-pass1");
    await pageB.getByRole("button", { name: "注册", exact: true }).click();
    // 等 register 落定（成功才关弹窗）再导航：避免请求被页面跳转中断
    await expect(pageB.getByRole("dialog")).toBeHidden({ timeout: 10_000 });
    // 隔离双断言（均确定性、无竞态，取代原 getByText toBeHidden——折叠列表下
    // DOM 无项目名文本，该断言恒过守卫不了泄漏回归）：
    //   a. 响应体空数组 —— 服务端按 user:<bob> 过滤的直接证据；waitForResponse
    //      的 url 匹配用 pathname 精确 ===，避免误捕获 /projects/:id/... 子路径
    //   b. 空列表时「继续先前项目」折叠入口不渲染 —— count 0 与响应体互证，无渲染竞态
    const listLoaded = pageB.waitForResponse(
      (r) => new URL(r.url()).pathname === "/projects" && r.request().method() === "GET",
    );
    await pageB.goto("/");
    const res = await listLoaded;
    expect(await res.json()).toEqual([]); // 服务端按 user:<bob> 过滤的直接证据
    await expect(pageB.getByRole("button", { name: /继续先前项目/ })).toHaveCount(0); // 空列表：折叠入口不渲染
  } finally { await ctxB.close(); }
});
