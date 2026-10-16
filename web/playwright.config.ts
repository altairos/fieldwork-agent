import process from "node:process";
import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright L4 端到端冒烟配置（Task 9，spec §6.L4）。
 * webServer 两段式：mock SSE 后端（MOCK_PORT=8300，匹配 vite proxy /projects→:8300）
 * + vite dev server（:5173）。纯 Node、确定性回放，无需 Python 后端/API key。
 * L4 门禁红线：测试不允许静默 skip（spec 内无 test.skip/fixme）。
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  // 跨 spec 也必须串行：mock server 的 settingsState 是全局单例，keys.spec 会翻转它，
  // 并行 worker 里 fieldwork.spec 的页面会被 403 门锁死（fullyParallel:false 只管单文件内）。
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium", testIgnore: /mobile\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    // 移动视口（批次 B）：Pixel 7 412×915（hasTouch/isMobile），仅跑 mobile.spec.ts；
    // 桌面 project 反向 testIgnore，避免同一 spec 双视口重跑（汉堡钮 lg:hidden 桌面不可见必挂）。
    { name: "mobile", testMatch: /mobile\.spec\.ts/, use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      // mock 后端（PORT=8300 匹配 vite proxy /projects→:8300）
      command: "npm run mock",
      env: { MOCK_PORT: "8300" },
      url: "http://localhost:8300/projects",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      // 前端 dev server（vite proxy 已配 /projects→:8300）
      command: "npm run dev",
      url: "http://localhost:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
