import { test, expect } from "@playwright/test";

/**
 * L4 完整剧本（spec §6.L4，Task 9）— 跑在扩展后的 mock server（纯 Node、确定性回放）：
 *   ① 项目集新建项目 → ② 工作台发消息触发回合 → ③ 工具卡价格与 mock payload 一致（¥180，
 *   价格不失真红线）→ ④ 航班降级如实呈现（降级 · flyai_unavailable）→ ⑤ SSE phase_transition
 *   推进到 B 田野点画像 → ⑥ 提案八章导航 + 正文渲染 → ⑦ 下载 .md 附件。
 *
 * CI 门禁红线：本文件不允许 test.skip / test.fixme 静默绕过；断言语义不可删，
 * UI 文案变化时只调整选择器。选择器与实际 DOM 的对应关系（已逐一核验组件源码）：
 *   - Hub 标题 = ProjectHub h1「田野罗盘」；创建按钮 = 输入框旁发送钮 aria-label「创建项目」
 *     （页面无其他「创建」字样控件，用 aria-label 精确匹配发送钮）
 *   - 工作台输入框 = Composer textarea aria-label「消息输入」，Enter 发送
 *   - 住宿卡 = collapsed 级搜索原语已移入中栏「检索结果」tab（EvidencePanel，spec C4）：
 *     先切 tab，再点「酒店搜索」检索行展开 HotelCard(SearchResultCard)，
 *     条目行 li 内 Price 渲染「¥180」（mock payload price=180）
 *   - 航班降级 = 工具活动条（ToolActivityStrip）如实转述「降级 · flyai_unavailable」
 *     （C4 后对话 tab 不再内联 collapsed 级卡片，失败信息由活动条承担）
 *   - 阶段推进 = PhaseBanner「阶段 B · 田野点画像」（LeftRail 常驻列出四阶段名，不可用裸「田野点画像」断言）
 *   - 八章导航 = ChapterNav nav[aria-label=提案章节导航]；正文 = ProposalViewer 的 markdown h2
 *   - 下载 = ProposalView 左栏「下载 .md」按钮（2026-10-18 Header 导出按钮已删，页面内唯一）
 */
test("L4 完整剧本：新建项目→工作台回合→工具卡价格→阶段推进→提案八章→下载", async ({ page }) => {
  // ① 首页：新建项目（h1「田野罗盘」用 heading role 精确匹配；kicker「FIELDWORK COMPASS」非 heading）
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "田野罗盘" })).toBeVisible();
  // 填一句话描述（Hub 页唯一 textarea；fill 触发 React onChange）
  const desc = page.locator("textarea").first();
  await desc.fill("研究西南少数民族村落的宗族组织变迁，单人驻村三个月，预算两万元");
  // 点创建按钮（aria-label「创建项目」→ POST /projects → navigate /p/:id）
  await page.getByRole("button", { name: "创建项目" }).click();
  // 跳转到工作台 /p/:id
  await expect(page).toHaveURL(/\/p\/[^/]+/, { timeout: 10_000 });

  // ② 工作台：发一条消息触发回合（两步式：POST /messages → SSE /events 脚本回放）
  const composer = page.getByLabel("消息输入");
  await composer.fill("帮我搜一下大理的住宿");
  await composer.press("Enter");

  // ③ 断言降级如实呈现（search_flights failed+degraded → 工具活动条如实标注；
  //    C4 后对话 tab 不再内联 collapsed 级卡片，失败信息由活动条承担）
  await expect(page.getByText(/降级 · flyai_unavailable/).first()).toBeVisible({ timeout: 15_000 });

  // ④ 断言价格与 mock payload 一致（search_hotels items[0].price = 180 → ¥180；
  //    C4：collapsed 级移入「检索结果」tab（EvidencePanel）——先切 tab，再展开「酒店搜索」检索行；
  //    scope 到「大理古城客栈」条目行内，排除卡片底部「最低 ¥180 · 最高 ¥180」摘要的重复匹配）
  await page.getByRole("tab", { name: "检索结果" }).click();
  const hotelEvidence = page.getByRole("button", { name: /酒店搜索/ }).first();
  await expect(hotelEvidence).toBeVisible({ timeout: 15_000 });
  await hotelEvidence.click();
  await expect(page.getByText("住宿搜索结果")).toBeVisible();
  const hotelRow = page.locator("li").filter({ hasText: "大理古城客栈" });
  await expect(hotelRow).toBeVisible();
  await expect(hotelRow.getByText("¥180")).toBeVisible();
  // 摘要行同源数字（同一 payload 的 cheapest/dearest，不换算不失真）
  await expect(page.getByText("最低 ¥180 · 最高 ¥180")).toBeVisible();

  // ⑤ 断言阶段推进（SSE phase_transition → field_site_profiles；PhaseBanner 主文切到「阶段 B · 田野点画像」）
  await expect(page.getByText("阶段 B · 田野点画像")).toBeVisible({ timeout: 15_000 });

  // ⑥ 提案：点 Header 视图 tab 走 SPA 进入 /p/:id/proposal。
  //   不能用整页 goto：匿名会话「离开即清记录」（useDisposeGuestProject）会在 pagehide 删项目，
  //   整页跳转后再加载该项目只能 404。站内切视图本来就该走路由，不重建文档。
  await page.getByRole("tab", { name: "方案草稿" }).click();
  await expect(page).toHaveURL(/\/proposal$/);
  // 八章导航可见（ChapterNav nav；正文 h2 同款文案在 nav 外，scope 到 nav 内避免 strict 双匹配）
  const nav = page.getByRole("navigation", { name: "提案章节导航" });
  await expect(nav.getByText("一 · 选题依据")).toBeVisible();
  await expect(nav.getByText("八 · 参考文献与附录")).toBeVisible();
  // 正文渲染（markdown h2 → heading role；ChapterNav 项是 button 非 heading，无歧义）
  await expect(page.getByRole("heading", { name: /研究设计与方法论/ })).toBeVisible();

  // ⑦ 下载 .md 触发（锚点 + Content-Disposition attachment → download 事件）
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /下载/i }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.md$/);
});
