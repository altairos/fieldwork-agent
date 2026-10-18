#!/usr/bin/env bash
# C2 部署：备份 -> 覆盖源码+dist -> restart -> 健康验证（幂等，可重复跑）
set -euo pipefail
APP=/opt/fieldwork-agent
SVC=fieldwork-web.service
TS=$(date +%m%d-%H%M)

echo "== 1) backup current =="
tar -czf "/opt/fw-backup-$TS.tar.gz" -C /opt --exclude='fieldwork-agent/.venv' fieldwork-agent
ls -la /opt/fw-backup-$TS.tar.gz

echo "== 2) overlay source + dist =="
tar -xzf /tmp/fw-src-789d5c6.tar.gz -C "$APP"
tar -xzf /tmp/fw-dist-789d5c6.tar.gz -C "$APP/web"
echo "bundle now: $(grep -o 'index-[A-Za-z0-9_-]*\.js' "$APP/web/dist/index.html" | head -1)"

echo "== 3) restart =="
systemctl restart "$SVC"

echo "== 4) health poll (max 30s) =="
ok=0
for i in $(seq 1 15); do
  sleep 2
  if curl -sf -m 3 http://127.0.0.1:2143/health >/dev/null; then ok=1; break; fi
done
if [ "$ok" != 1 ]; then echo "!! /health not responding"; journalctl -u "$SVC" -n 30 --no-pager; exit 1; fi

echo "== health =="
curl -s -m 5 http://127.0.0.1:2143/health; echo
echo "== ready =="
curl -s -m 10 http://127.0.0.1:2143/ready; echo
echo "== auth: /projects without token (expect 401) =="
curl -s -o /dev/null -w '%{http_code}\n' -m 5 http://127.0.0.1:2143/projects
echo "== mcp route present (non-404) =="
curl -s -o /dev/null -w '%{http_code}\n' -m 5 http://127.0.0.1:2143/mcp
echo "== service =="
systemctl is-active "$SVC"
echo "ALL_STEPS_DONE"
