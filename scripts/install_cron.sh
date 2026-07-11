#!/bin/bash
# 安装本机 cron：每天 01:00 触发；实际是否跑由 interval_days=5 门禁决定
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LINE="0 1 * * * cd ${ROOT} && ./scripts/periodic_run.sh >> logs/cron.log 2>&1"

chmod +x "${ROOT}/scripts/periodic_run.sh" \
  "${ROOT}/scripts/weekly_run.sh" \
  "${ROOT}/scripts/daily_run.sh" \
  "${ROOT}/scripts/install_cron.sh"

TMP="$(mktemp)"
(crontab -l 2>/dev/null | grep -v 'money_more' | grep -v 'periodic_run.sh' || true) >"$TMP"
echo "$LINE" >>"$TMP"
crontab "$TMP"
rm -f "$TMP"

echo "已安装 cron："
crontab -l | grep periodic_run || true
echo
echo "说明：cron 每天 01:00 触发；未满 5 天会自动跳过。"
echo "强制立刻跑一轮：cd ${ROOT} && money-more scheduled --force"
