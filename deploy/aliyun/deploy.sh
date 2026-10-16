#!/usr/bin/env bash
# 田野罗盘 — 阿里云 (Alibaba Cloud Linux / Anolis, dnf + systemd) 一键部署脚本
# 用法：把项目解压到服务器后，在项目根目录执行：  sudo bash deploy/aliyun/deploy.sh
# 幂等：可重复运行（重建 venv、重装依赖、刷新 systemd 单元并重启）。
set -euo pipefail

PORT=2143
# 脚本所在目录 = <APP_DIR>/deploy/aliyun/deploy.sh → 回推 APP_DIR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SVC=fieldwork-web.service
UNIT_SRC="$SCRIPT_DIR/$SVC"
ENV_EXAMPLE="$SCRIPT_DIR/fieldwork-web.env"
ENV_EXAMPLE_SRC="$SCRIPT_DIR/fieldwork-web.env.example"

echo "==> APP_DIR = $APP_DIR"
echo "==> PORT    = $PORT"

# 0) root 检查（systemd/写 /etc 需要）
if [ "$(id -u)" -ne 0 ]; then
  echo "!! 请用 root 运行：sudo bash deploy/aliyun/deploy.sh" >&2
  exit 1
fi

# 1) 校验 Python >= 3.10
PYBIN="$(command -v python3 || true)"
if [ -z "$PYBIN" ]; then echo "!! 未找到 python3，请先 dnf install -y python3" >&2; exit 1; fi
"$PYBIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    sys.exit("!! 需要 Python >= 3.10，当前 %s" % sys.version.split()[0])
print("==> Python", sys.version.split()[0], "OK")
PY

# 2) 系统依赖（编译个别 sdist 时需要 gcc/python3-devel；已装则秒过）
echo "==> 安装系统依赖 (dnf)"
dnf install -y python3-pip python3-devel gcc || {
  echo "!! dnf 安装失败，若已具备 gcc/python3-devel 可忽略"; }

# 3) 虚拟环境
cd "$APP_DIR"
if [ ! -d .venv ]; then
  echo "==> 创建虚拟环境 .venv"
  "$PYBIN" -m venv .venv
fi
echo "==> 升级 pip"
.venv/bin/pip install --upgrade pip wheel >/dev/null
echo "==> 安装项目 (pip install -e .) —— 首次会拉 fastapi/uvicorn/pydantic/dashscope，稍候"
.venv/bin/pip install -e .

# 4) 前端产物检查（打包时已内置 web/dist；缺失则提示）
if [ ! -f "$APP_DIR/web/dist/index.html" ]; then
  echo "!! 未找到 web/dist/index.html —— 前端未构建。API 仍可跑，但页面无前端。" >&2
  echo "   需在有 Node 的机器上 cd web && npm ci && npm run build 后再上传 dist。" >&2
else
  echo "==> 前端产物 web/dist 就绪"
fi

# 5) 环境变量文件（不存在才从模板创建，避免覆盖已填密钥）
if [ ! -f "$ENV_EXAMPLE" ]; then
  cp "$ENV_EXAMPLE_SRC" "$ENV_EXAMPLE"
  echo "==> 已生成 $ENV_EXAMPLE （请编辑填入 DASHSCOPE_API_KEY）"
fi
chmod 600 "$ENV_EXAMPLE"

# 6) 安装 systemd 单元（按实际 APP_DIR 重写路径，兼容非 /opt 部署）
echo "==> 安装 systemd 单元 $SVC"
sed "s#/opt/fieldwork-agent#$APP_DIR#g" "$UNIT_SRC" > "/etc/systemd/system/$SVC"
systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1 || true
systemctl restart "$SVC"

# 7) 本地冒烟
sleep 2
echo "==> 服务状态："
systemctl --no-pager --full status "$SVC" | head -n 12 || true
echo "==> 本地健康检查 http://127.0.0.1:$PORT/health ："
if command -v curl >/dev/null 2>&1; then
  curl -s -m 5 "http://127.0.0.1:$PORT/health" && echo || echo "(暂无响应，看 journalctl -u $SVC -n 50)"
else
  echo "(未装 curl，可 dnf install -y curl 后再测)"
fi

cat <<EOF

============================================================
 部署完成。下一步：
 1) 编辑密钥：  vi $ENV_EXAMPLE   然后  systemctl restart $SVC
 2) 放行端口：  阿里云控制台 → 轻量应用服务器 → 防火墙 → 添加规则
                协议 TCP、端口 $PORT、来源 0.0.0.0/0（或限你自己的 IP 更安全）
 3) 浏览器访问：http://<服务器公网IP>:$PORT/
 4) 看日志：    journalctl -u $SVC -f
============================================================
EOF
