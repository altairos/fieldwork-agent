import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/projects": "http://localhost:8300",
      "/auth": "http://localhost:8300", // 登录/注册四端点（账号隔离一期，dev/e2e 代理到 mock）
      "/health": "http://localhost:8300",
      "/settings": "http://localhost:8300", // key 配置状态（ApiKeyCard/Workspace 消费）
      "/__test": "http://localhost:8300", // e2e 专用翻转端点（mock server）
    },
  },
  build: { outDir: "dist" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    // tests/e2e 是 Playwright L4 剧本（@playwright/test），不归 vitest 跑（Task 9）
    exclude: ["**/node_modules/**", "**/dist/**", "tests/e2e/**"],
  },
});
