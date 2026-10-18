import { test, expect } from "@playwright/test";

/**
 * 移动端冒烟（批次 B；Pixel 7 412×915，hasTouch/isMobile）— 断言批次 A 的移动形态：
 *   ① 首页/工作台/提案页单栏无横向溢出（grid lg: 回落）
 *   ② Header 汉堡开左栏抽屉，backdrop 点击关闭
 *   ③ 右栏折叠为底部面板，把手展开 RightDock（Tabs 可见）
 *   ④ 提案页单栏：八章导航可见（左栏限高 46vh 容器内）
 *
 * CI 门禁红线：本文件不允许 test.skip / test.fixme 静默绕过。
 * 选择器与实际 DOM 的对应关系（已逐一核验组件源码）：
 *   - 汉堡钮 = Header aria-label「打开阶段导航」（onMenu 仅 Workspace 传入；ProposalView
 *     不传不渲染，故本 spec 提案段不断言汉堡钮）
 *   - 抽屉内容 = LeftRail 节标签「方案规划阶段」（lg- 收起态 wrapper 为 display:none，
 *     toBeHidden 可断言）；「A · 研究定位」是多源文案（LeftRail 阶段钮/
 *     PhaseBanner「阶段 A」/右栏状态投影/进度行），四源中仅阶段项为 button，
 *     用 getByRole("button") 消歧
 *   - 把手 = App.tsx 右栏 button「上下文面板 · 状态投影」；RightDock 面板头有同文案
 *     div，用 button role 消歧；展开断言用 Tabs 的 role=tab「访谈对象」
 *   - backdrop = aria-hidden 遮罩 div（无语义 role），用点击抽屉外区域（x=392 > 抽屉宽
 *     268px）命中验证关闭行为
 */
test("移动端冒烟：单栏无横溢 + 汉堡抽屉开关 + 右栏折叠面板 + 提案页单栏", async ({ page }) => {
  // ① 首页（ProjectHub）加载 + 无横向溢出
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "田野罗盘" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  // 新建项目进工作台（mock 流程同 fieldwork.spec）
  await page.locator("textarea").first().fill("研究西南少数民族村落的宗族组织变迁，单人驻村三个月，预算两万元");
  await page.getByRole("button", { name: "创建项目" }).click();
  await expect(page).toHaveURL(/\/p\/[^/]+/, { timeout: 10_000 });

  // ② 工作台单栏：无横溢；汉堡钮可见；左栏抽屉默认收起（hidden 不可见）
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const burger = page.getByRole("button", { name: "打开阶段导航" });
  await expect(burger).toBeVisible();
  await expect(page.getByText("方案规划阶段")).toBeHidden();

  // 开抽屉 → 左栏阶段导航可见
  await burger.click();
  await expect(page.getByText("方案规划阶段")).toBeVisible();
  await expect(page.getByRole("button", { name: /A · 研究定位/ })).toBeVisible();

  // backdrop 点击关闭（点击抽屉外右侧区域命中遮罩）
  await page.mouse.click(392, 458);
  await expect(page.getByText("方案规划阶段")).toBeHidden();

  // ③ 右栏底部面板：把手展开 → RightDock Tabs 可见；再点收起
  const handle = page.getByRole("button", { name: "上下文面板 · 状态投影" });
  await expect(handle).toBeVisible();
  await handle.click();
  await expect(page.getByRole("tab", { name: "访谈对象" })).toBeVisible();
  await handle.click();
  await expect(page.getByRole("tab", { name: "访谈对象" })).toBeHidden();

  // 展开过面板后仍无横向溢出
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  // ④ 提案页单栏：八章导航可见（左栏限高容器内）+ 无横溢。导航项与正文 h2 同文案，
  //    scope 到 ChapterNav nav 避免严格模式双匹配（同 fieldwork.spec ⑥）
  const url = new URL(page.url());
  await page.goto(`${url.origin}${url.pathname}/proposal`);
  const nav = page.getByRole("navigation", { name: "提案章节导航" });
  await expect(nav.getByText("一 · 选题依据")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
