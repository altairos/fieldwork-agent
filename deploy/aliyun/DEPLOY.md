# 田野罗盘 · 阿里云轻量服务器部署说明

目标环境：**Alibaba Cloud Linux 4（Anolis/龙蜥，dnf + systemd，Python 3.11.4）**
部署形态：**真实完整版** —— FastAPI 后端（`web_host`，含真实 agent 内核）+ 内置前端（`web/dist`），
前后端**同源**跑在一个 uvicorn 进程里（`app.py` 已内建挂载 dist + SPA fallback，无需处理 CORS）。

监听端口 **2143**。域名备案完成前，用 `http://<公网IP>:2143/` 临时访问。

---

## 0. 架构与前提（务必先读）

- 服务入口不是 `fieldwork-web` 脚本（它写死 `127.0.0.1:8300`，外部访问不到），而是直接用
  `uvicorn fieldwork_agent.web_host.app:create_app --factory --http h11 --host 0.0.0.0 --port 2143`。
  `--http h11` 是**硬要求**：百炼探测节点（Java 客户端）默认发 h2c 明文升级头，uvicorn 默认
  httptools 实现会拒为 400（详见第 7 节末尾注意事项）。
- 运行密钥走**环境变量**（systemd `EnvironmentFile`）：
  - `DASHSCOPE_API_KEY`（Web 直访**必填**，通义千问/百炼）——没有它页面 LLM 不可用，对话/方案
    生成失效；纯 MCP 托管模式可留空（调用方用 `x-dashscope-key` 动态 Header 自带 key，见第 7 节）。
  - `FLYAI_API_KEY`（可选，飞猪）——服务器**全局兜底** key：未带 `x-flyai-key` 的飞猪调用都
    消耗它；不填且调用方也没带，则机票/火车/酒店检索工具降级，其余功能正常（详见第 7 节）。
- 项目数据存于运行用户的 `~/.fieldwork_agent/projects`（本机 root → `/root/.fieldwork_agent/projects`）。
- **Tier S 资产（`secrets/`）默认不在部署包内**，服务会回落到内置 **Tier P 公开版**提示词/访谈库，
  功能完整、仅提示词为开源简版。要用生产版见第 6 节。
- **前置依赖**：`curl`（健康检查）——部署脚本步骤 2 已包含 `dnf install -y curl`。
- **旅行搜索依赖**：`nodejs + npm + @fly-ai/flyai-cli`——部署脚本检测到 npm 会自动安装；
  不装只影响 `/ready` 的 `flyai_cli` 项（页面与其他功能正常，旅行搜索不可用）。Web 与 MCP
  两条路径的飞猪工具（8 个 search 工具）都经该 CLI 子进程出网，key 优先级：请求头
  `x-flyai-key`（调用方的）→ env `FLYAI_API_KEY`（服务器兜底）。
- **公网令牌**：公网部署建议设置 `FIELDWORK_APP_TOKEN` 访问令牌（所有 `/projects`、`/settings`
  接口要求 `X-App-Token` 请求头匹配）——说明见 `deploy/aliyun/fieldwork-web.env.example` 注释段。

---

## 1. 本地打包（Windows，已完成）

打包产物：`fieldwork-agent-deploy.tar.gz`（含源码 + 预构建 `web/dist` + 本 `deploy/` 目录；
已排除 `.git/.venv/node_modules/缓存/secrets`）。服务器**无需装 Node**，因为 dist 已内置。

如需重新打包，见第 9 节。

---

## 2. 上传到服务器（Windows → 阿里云）

Windows 10/11 自带 OpenSSH 客户端。在 **PowerShell 或 CMD**（不是 Git Bash）里执行，
把 `<IP>` 换成你的公网 IP、把路径换成 tar.gz 的实际位置：

```powershell
scp F:\Agents_dev\Fieldwork_Agent\fieldwork-agent-deploy.tar.gz root@<IP>:/opt/
```

首次连接会问 yes/no，输入服务器 root 密码。

> 备选：也可用轻量服务器控制台自带的「文件上传」网页工具上传该 tar.gz 到 `/opt/`。

---

## 3. 解压 + 一键部署

SSH 登录服务器：

```bash
ssh root@<IP>
```

解压到 `/opt/fieldwork-agent` 并运行部署脚本：

```bash
mkdir -p /opt/fieldwork-agent
tar -xzf /opt/fieldwork-agent-deploy.tar.gz -C /opt/fieldwork-agent
cd /opt/fieldwork-agent
sudo bash deploy/aliyun/deploy.sh
```

脚本会：装系统依赖 → 建 `.venv` → `pip install -e .` → 检测/安装 flyai CLI → 生成 env 模板 →
按实际路径安装 systemd 单元 → 启动 → 本地 `/health` + `/ready` 冒烟（失败即报错退出）。
看到 `/health ok` 与 `/ready ok` 即部署成功。

---

## 4. 填密钥

```bash
vi /opt/fieldwork-agent/deploy/aliyun/fieldwork-web.env
# 填入 DASHSCOPE_API_KEY=sk-xxxx   （可选 FLYAI_API_KEY=）
# 可选 FIELDWORK_APP_TOKEN=（公网部署建议设置，前端设置卡填同一令牌）
# 保存后重启：
systemctl restart fieldwork-web.service
```

env 文件已 `chmod 600`，且不在打包/提交范围内，密钥不会外泄。

---

## 5. 放行端口 2143 + 访问

**阿里云控制台 → 轻量应用服务器 → 你的实例 → 防火墙（安全组）→ 添加规则：**

| 项 | 值 |
|---|---|
| 应用类型 | 自定义 |
| 协议 | TCP |
| 端口范围 | 2143 |
| 授权对象/来源 | `0.0.0.0/0`（图省事）或 `你的公网IP/32`（**更安全，推荐**） |

> 服务器内若还开了 firewalld，另需：`firewall-cmd --add-port=2143/tcp --permanent && firewall-cmd --reload`

放行后浏览器访问：**`http://<公网IP>:2143/`**

⚠️ 备案期间只能用 IP + 非标端口（2143）。**不要**把未备案域名指过来走 80/443，会被网关拦截，
硬绕（域名+非标端口）有被扫描封停风险。

---

## 6.（可选）启用 Tier S 生产版资产

若要让线上用生产版系统提示词/访谈库（而非开源简版），把本地 `secrets/` 单独安全上传：

```powershell
# Windows：单独打包 secrets 再上传（不要把密钥类文件混进公开包）
scp -r F:\Agents_dev\Fieldwork_Agent\secrets root@<IP>:/opt/fieldwork-agent/
```

然后在 env 文件里加一行并重启：

```
FIELDWORK_ASSETS_DIR=/opt/fieldwork-agent/secrets
```

> `secrets/` 属技术秘密（Tier S，独立私有仓库管理）。是否上传由你决定；上传后确保服务器访问受控。

---

## 7. 百炼 MCP 接入（自定义 MCP + 多租户 key 归属）

Web 进程同时暴露 MCP 端点 **`/mcp`**（Streamable HTTP，与页面同端口）。在阿里百炼控制台
「Agent 编排 → MCP 列表 → 添加自定义 MCP Server」接入：

| 配置项 | 值 |
|---|---|
| 服务地址 | `http://<公网IP>:2143/mcp`（备案后换 `https://<域名>/mcp`，见第 8 节） |
| 鉴权 | 自定义 Header `X-App-Token: <与 env FIELDWORK_APP_TOKEN 同值>` |
| 可选 Header | `x-flyai-key` / `x-dashscope-key` —— 调用方自带 key，逐请求覆盖服务器 env |

### key 归属（哪次调用消耗谁的额度）

| 调用层 | 优先级链 | 全部缺失时 |
|---|---|---|
| Agent 对话推理 | **调用方自己的百炼账号**（发生在百炼平台侧，不经过本服务器） | — |
| MCP 内 LLM 工具（方案/访谈指南/纪要提取） | 请求头 `x-dashscope-key`（调用方的）→ env `DASHSCOPE_API_KEY`（服务器兜底） | 模板降级，零额度消耗 |
| 飞猪搜索（8 个 search 工具 + 行程组装） | 请求头 `x-flyai-key`（调用方的）→ env `FLYAI_API_KEY`（服务器兜底） | `flyai_unavailable` 降级 |

- env 里配的 key 是**全局兜底**：所有未带 Header 的调用共用（飞猪免费额度：注册赠送
  5000 次 + 每日 100 次共享）。
- 要让每个用户自理配额：在其百炼 MCP 配置里加 `x-flyai-key` / `x-dashscope-key` Header
  即可——覆盖只在本次请求内生效、请求结束即复位，互不串号。
- 本生产实例当前形态：`DASHSCOPE_API_KEY` 留空（LLM 工具靠动态 Header，无则模板降级、
  零百炼消耗）+ `FLYAI_API_KEY` 填站主 key 兜底。
- 动态 Header 机制实现见 `mcp_server/request_ctx.py`（ASGI 中间件 + contextvar，逐请求
  重建 FlyAIClient / LLMClient）。

> ⚠️ **不要丢 `--http h11`**：百炼探测节点是 Java 客户端，会发送 HTTP/2 明文升级（h2c）
> 头，uvicorn 默认 httptools 实现直接拒为 400，百炼侧现象为「该 MCP 暂无可用工具，无法
> 添加」。systemd 单元已固定 `--http h11` 规避；手工重启或改启动命令时务必保留该参数。

---

## 8. 备案完成后：域名 + HTTPS

1. 域名 A 记录 → 服务器公网 IP。
2. `dnf install -y nginx`，把 `deploy/aliyun/nginx-fieldwork.conf` 里的 `your-domain.com`
   换成已备案域名，复制到 `/etc/nginx/conf.d/fieldwork.conf`。
3. 申请免费 SSL 证书（阿里云 SSL 或 certbot），启用配置里的 443 段与证书路径。
4. `nginx -t && systemctl enable --now nginx`；防火墙放行 80/443。
5. Nginx 反代已针对 **SSE 关闭 `proxy_buffering`**，流式逐字输出正常。

之后可把 2143 的公网放行收回（只留 Nginx 反代本机 127.0.0.1:2143）。

---

## 9. 日常运维

```bash
systemctl status  fieldwork-web.service     # 状态
systemctl restart fieldwork-web.service     # 改 env/代码后重启
journalctl -u fieldwork-web.service -f      # 实时日志
```

**更新代码**（本地重新打包上传后）：

```bash
tar -xzf /opt/fieldwork-agent-deploy.tar.gz -C /opt/fieldwork-agent
cd /opt/fieldwork-agent && .venv/bin/pip install -e . && systemctl restart fieldwork-web.service
```

> 重新打包命令（Windows Git Bash，在项目根）：
> ```bash
> tar --exclude=.git --exclude=.venv --exclude=node_modules --exclude=__pycache__ \
>     --exclude='*.pyc' --exclude='*.egg-info' --exclude=.pytest_cache --exclude=.ruff_cache \
>     --exclude=.mypy_cache --exclude=.worktrees --exclude=.workbuddy --exclude=.superpowers \
>     --exclude=secrets --exclude=.qw-pages-deploy --exclude=.fieldwork_agent --exclude=.env \
>     --exclude=web/node_modules --exclude=web/playwright-report --exclude=web/test-results \
>     --exclude=fieldwork-agent-deploy.tar.gz \
>     -czf fieldwork-agent-deploy.tar.gz .
> ```
> 前端有改动时，先在本地 `cd web && npm run build` 刷新 `web/dist` 再打包（dist 已随包上传，服务器不需 Node）。

---

## 10. 排错

| 现象 | 排查 |
|---|---|
| 浏览器连不上，但服务器本地 `curl 127.0.0.1:2143/health` 通 | 防火墙/安全组没放行 2143（第 5 节） |
| `502`/页面无前端 | `web/dist/index.html` 是否存在；`journalctl -u fieldwork-web -n 50` |
| 对话无回复、日志报 LLM 相关 | `DASHSCOPE_API_KEY` 没填或填错 → 改 env 后 `systemctl restart` |
| 机票/酒店搜索显示不可用 | 未配 `FLYAI_API_KEY`，属预期降级 |
| 端口被占用启动失败 | `ss -ltnp | grep 2143` 查占用，或改端口（同步改 service 与本文档） |
| 提示词像是简版 | 未启用 Tier S，见第 6 节 |
| 接口 401 `app_token_required` | 服务端设了 FIELDWORK_APP_TOKEN 但浏览器未填/填错——首页「API 密钥」卡填写同一令牌 |
| `/ready` 返回 `not_ready` | 按响应里 checks 找 false 项：`flyai_cli=false` 装 CLI；`projects_dir_writable=false` 查目录属主 |
| 百炼添加 MCP 后提示「暂无可用工具」 | ① 启动命令是否带 `--http h11`（h2c 400，见第 7 节）；② `X-App-Token` 与 env `FIELDWORK_APP_TOKEN` 是否一致；③ `journalctl -u fieldwork-web -n 50` 看有无 `POST /mcp` 记录 |
| MCP 飞猪工具报 `flyai_rate_limited` | 服务器兜底 key 配额耗尽——让调用方在百炼 MCP 配置加 `x-flyai-key` 自理，或更换更高额度 key |
