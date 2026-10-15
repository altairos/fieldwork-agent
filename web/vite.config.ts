import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/projects": "http://localhost:8300",
      "/health": "http://localhost:8300",
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
