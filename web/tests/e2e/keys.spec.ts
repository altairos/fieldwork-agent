import { test, expect, type Request } from "@playwright/test";

/**
 * 首页 API key 鉴权端到端 — 跑在 mock server（8300，经 vite proxy）：
 *   ① 服务端未配（POST /__test/settings 翻转）+ 浏览器无 key → 工作台「可浏览不可发」
 *      （快照/时间线照常渲染，Composer textarea disabled + 锁提示）
 *   ② 首页「密钥 / 登录」弹层保存 key → 「浏览器已存」徽标 → 返回工作台输入解锁
 *   ③ 发送 → 请求头携带 X-DashScope-Key（page.on("request") 断言）→ mock 回合正常回放
 *
 * 翻转/恢复直连 mock 端口（Node fetch；afterAll 无 fixture，不走 playwright request）。
 * serial：翻转 → 保存 → 发送对同一份 mock 状态有顺序依赖；afterAll 恢复默认
 * （configured:true 建模阿里云部署形态），不污染后续 spec（config fullyParallel:false）。
 * Playwright 每 test 独立 context，localStorage 天然隔离——无需手动清理。
 */
test.describe.configure({ mode: "serial" });

const MOCK = "http://localhost:8300";

async function setMockSettings(state: { dashscope_configured: boolean; flyai_configured: boolean }) {
  const resp = await fetch(`${MOCK}/__test/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  });
  expect(resp.ok).toBeTruthy();
}

test.afterAll(async () => {
  await setMockSettings({ dashscope_configured: true, flyai_configured: true });
});

test("无 key：可浏览不可发；首页保存后解锁并携带鉴权头发送", async ({ page }) => {
  // 翻转 mock 为「服务端未配」，模拟无环境变量部署
  await setMockSettings({ dashscope_configured: false, flyai_configured: false });

  // ① 工作台锁定：先 UI 创建项目（Task 9 注入 owner 头 → mock 归属登记；
  //    直接 goto 固定 id 在归属化 mock 下会 404，故创建后复用本会话 URL）
  await page.goto("/");
  await page.locator("textarea").first().fill("研究大理乡村旅居，单人两周，预算一万元");
  await page.getByRole("button", { name: "创建项目" }).click();
  await expect(page).toHaveURL(/\/p\/[^/]+/, { timeout: 10_000 });
  const projectUrl = page.url();
  // 可浏览：快照加载（Header 项目名来自 research_context.topic；归属相符即 fakeSnapshot）。
  //   getByText 会同时命中右栏「研究主题」textarea 的同值 value（Playwright 按 value 匹配），
  //   scope 到 banner 消歧（目标断言就是 Header 项目名）
  await expect(page.getByRole("banner").getByText("大理乡村旅居研究")).toBeVisible({ timeout: 10_000 });
  // 不可发：textarea 禁用 + 锁提示 + 发送键禁用
  await expect(page.getByLabel("消息输入")).toBeDisabled();
  await expect(page.getByTestId("composer-lock-hint")).toContainText("未配置百炼 API Key");
  await expect(page.getByRole("button", { name: "发送" })).toBeDisabled();

  // ② 首页密钥弹层（右上角「密钥 / 登录」入口 → KeyDialog）：保存 key → 徽标翻转「浏览器已存」
  await page.goto("/");
  await page.getByRole("button", { name: "密钥 / 登录" }).click();
  await expect(page.getByText("API 密钥", { exact: true })).toBeVisible();
  // password 输入按顺序：百炼（必填）→ 访问令牌（飞猪 Key 已收进高级选项，默认隐藏）
  await page.locator('input[type="password"]').first().fill("sk-e2e-dashscope");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("浏览器已存").first()).toBeVisible();
  await expect(page.getByText("已保存")).toBeVisible();

  // ③ 返回工作台：解锁（本地 key 优先生效，免查直通）→ 发送并断言鉴权头
  await page.goto(projectUrl);
  await expect(page.getByRole("banner").getByText("大理乡村旅居研究")).toBeVisible();
  await expect(page.getByLabel("消息输入")).toBeEnabled();

  const headersSeen: Record<string, string> = {};
  page.on("request", (req: Request) => {
    if (req.url().includes("/messages") && req.method() === "POST") {
      Object.assign(headersSeen, req.headers());
    }
  });
  await page.getByLabel("消息输入").fill("帮我搜一下大理的住宿");
  await page.getByLabel("消息输入").press("Enter");
  // 回合正常回放（mock 门放行：请求头带了页面 key）；
  // C4：collapsed 级搜索卡移入「检索结果」tab（EvidencePanel），切 tab 后断言酒店检索行到达
  await page.getByRole("tab", { name: "检索结果" }).click();
  await expect(page.getByRole("button", { name: /酒店搜索/ }).first()).toBeVisible({ timeout: 15_000 });
  expect(headersSeen["x-dashscope-key"]).toBe("sk-e2e-dashscope");
});
