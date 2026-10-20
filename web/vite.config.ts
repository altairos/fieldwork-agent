import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/** react-markdown / remark-gfm 及其传递依赖（unified 生态）。漏掉的会落 vendor，不影响正确性。 */
const MARKDOWN_PKGS =
  /^(react-markdown|remark-.+|rehype-.+|micromark.*|mdast-.+|hast-.+|unified|unist-.+|vfile.*|bail|trough|devlop|is-plain-obj|extend|style-to-object|inline-style-parser|character-entities.*|decode-named-character-reference|property-information|space-separated-tokens|comma-separated-tokens|html-url-attributes|markdown-table|ccount|escape-string-regexp|trim-lines|longest-streak|zwitch)$/;

export default defineConfig({
  // 子路径部署（nginx https://miovelle.cn/fieldpass/）：构建时注入 VITE_BASE=/fieldpass/，
  // 让 index.html 与 JS 内 public 资源都带前缀；默认 "/" 保持本地 8300/8301/e2e 不变。
  base: process.env.VITE_BASE || "/",
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
  build: {
    outDir: "dist",
    rollupOptions: {
      output: {
        // 依赖分包（评审 R9）：路由级 lazy 只拆得动「应用代码」，第三方依赖若不加这块
        // 会全部并进主 chunk（原先单 chunk 560 kB，构建器已警告 >500 kB）。
        // 分三组：react 栈（几乎不动，长期缓存）/ markdown 栈（只有对话正文与提案阅读器要，
        // 首页 `/` 完全不需要——单拎出来能把它从首屏关键路径上摘掉）/ 其余 vendor。
        // 注：Vite 8（Rolldown）的 manualChunks 只接受函数形式，不接受对象映射。
        // 名单漏掉的传递依赖会落到 vendor，只是分包不够细，不会出错。
        manualChunks(id: string) {
          const hit = /[\\/]node_modules[\\/]((?:@[^\\/]+[\\/])?[^\\/]+)/.exec(id);
          if (!hit) return undefined;
          const pkg = hit[1];
          if (/^(react|react-dom|scheduler|react-router|react-router-dom)$/.test(pkg)) {
            return "react-vendor";
          }
          if (MARKDOWN_PKGS.test(pkg)) return "markdown";
          return "vendor";
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    // 并发上限（2026-10-19 实测）：默认按 CPU 核数开 worker，35 个 jsdom 文件下属于
    // **过度订阅**——全量耗时反而从 59s 涨到 86s，且首个 App 集成用例（默认 5s 超时）
    // 间歇性假红（单独跑 0.5s 过，全量跑 6.3s 超时）。限到 4 既快又稳，小机器 CI 同理。
    maxWorkers: 4,
    // tests/e2e 是 Playwright L4 剧本（@playwright/test），不归 vitest 跑（Task 9）
    exclude: ["**/node_modules/**", "**/dist/**", "tests/e2e/**"],
  },
});
