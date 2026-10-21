# TODO — 审计遗留项

> 来源：`docs/reviews/2026-10-20-代码质量架构安全审计.md` §9（该目录被 gitignore，报告仅本地留存）。
> 本文件只列**已确认存在、但当前有明确阻塞**的项；已完成项见报告 §9 批次表，不再重复。
> 最近更新：2026-10-21（A09 完成后）。

## 1. S08（P3）：前端 localStorage 明文保存 API Key

- **现状**：`web/src/lib/keys.ts` 与 `web/src/lib/session.ts` 把 DashScope / FlyAI / 高德 /
  App Token 与会话令牌写入 `localStorage`；已验证无 `rehype-raw`、无
  `dangerouslySetInnerHTML`，当前 XSS 面很小。风险主要在共享终端与未来引入富文本渲染时。
- **阻塞原因**：属产品决策——需要「仅本次会话保存」的开关与默认值（默认持久化更符合田野
  离线使用习惯），并要同步文案、设置面板与用户预期；不是纯技术改动。
- **下一步**：
  1. 产品确认开关位置（设置 → 隐私）与默认值；
  2. `keys.ts` / `session.ts` 抽象 storage 适配层，`persist=false` 时全部走 `sessionStorage`；
  3. 在安全文档明示取舍。
- **验收**：选择「仅本次会话」后关闭标签页，四项 Key 与令牌不再存在；`vitest` 覆盖两种模式。

## 2. A07（P2）：供应链门禁剩余三项

### 2.1 Python 依赖锁文件

- **现状**：无 `uv.lock` / `requirements.lock`；CI 按 `pyproject.toml` 下限解析，构建不可复现。
- **阻塞原因**：本机访问 PyPI / 安装 `uv` 受网络限制（此前轮次实测受阻），无法在仓库内生成并
  验证锁文件。
- **下一步**：在可联网环境执行 `uv lock`（或 `pip-compile --generate-hashes`），提交锁文件；
  CI 改为 `uv sync --frozen`（或 `pip install -r requirements.lock`）。
- **验收**：干净环境两次安装得到相同版本集合；CI 在锁文件与 `pyproject.toml` 不一致时失败。

### 2.2 前端 ESLint

- **现状**：`web/package.json` 无 `lint` 脚本、无 ESLint 配置。
- **阻塞原因**：项目使用 TypeScript 7，`typescript-eslint` 的 peer 范围尚未覆盖（此前实测
  peer 冲突），强装会产生不可信的 lint 结果。
- **下一步（三选一，需定调）**：等 typescript-eslint 支持 TS 7；或改用 Biome（自带 TS 解析）；
  或临时降 TS 版本（不推荐，影响构建链）。
- **验收**：`npm run lint` 在 CI 中执行且对 `web/src` 零 error。

### 2.3 Tailwind 3 dev-only high 漏洞

- **现状**：`npm audit` 报 Tailwind 3 链路的 dev-only high；运行时依赖无 high/critical。
- **阻塞原因**：修复需升级 Tailwind 4（破坏性变更，涉及现有 token 与构建配置），须与视觉回归
  同批做，不能顺手升。
- **下一步**：单开一轮 Tailwind 4 迁移，跑 `vitest` + 截图对比后再合并。
- **验收**：`npm audit --audit-level=high` 无 high；视觉无回归。

## 3. S01（P1）剩余：nginx 443 模板仍为「拿到证书后再启用」

- **现状**：公网绑定 + 空令牌 fail-open 已在代码层修复（启动拒绝裸奔）；`deploy/aliyun` 的
  nginx 模板仍只开 80，TLS 配置以注释形式预留；`DEPLOY.md` §8 已把「收回 2143 端口」列为必做。
- **阻塞原因**：需要真实域名与证书签发（DNS 指向、certbot 执行），仓库内无法验证，改模板反而
  可能让部署脚本在无证书环境启动失败。
- **下一步**：证书就绪后填入模板 server 块（443 + HSTS + 80→443 跳转），执行
  `nginx -t && systemctl reload nginx`，再用外部扫描确认 2143 关闭。
- **验收**：`https://<域名>` 可用、HTTP 自动跳转、2143/8301 不再对外。

## 4. A09 后续收尾（非阻塞，观察期后做）

- **现状**：`Config` 已下沉 `kernel/config.py`；`core/config.py` 仅为旧 import 路径保留
  re-export（不转发 monkeypatch），`tests/test_layer_dependencies.py` 钉住「宿主对 core 归零」。
- **下一步**：确认无外部脚本仍从 `core.config` 取配置后，删除兼容层，并把 `core/cli.py`、
  `core/orchestrator.py`、`core/__init__.py` 改为直接 import `kernel.config`。
- **验收**：全仓 `grep core.config` 为零；删除后全量测试仍绿。

## 5. 已知边界（风险接受，非 TODO）

- **S06**：匿名地图必须下发 `amap_js_key` / `amap_js_security`，`/settings` 已受 token 门禁；
  依据写在 `web_host/api/settings.py` docstring。若将来地图改为登录后加载，应改为布尔 +
  构建期注入。
- **A04**：`uvicorn --workers N` 为 CLI 参数，进程内不可见；启动断言只覆盖环境变量路径，
  部署文档已明确禁止该参数。
