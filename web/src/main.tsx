import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { applyTheme, readStoredTheme } from "./lib/theme";
import "./styles/themes.css";
import "./styles/tokens.css";

// 双保险：index.html 内联脚本已先行设置（防 FOUC），此处覆盖一次，
// 保证直接挂载（无完整 HTML 壳，如测试）时主题也生效。
applyTheme(readStoredTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
