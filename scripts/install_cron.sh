#!/bin/bash
# 安装本机 cron：每天 01:00 触发；实际是否跑由 interval_days=5 门禁决定
#
# macOS 注意：项目在 ~/Documents 时，cron 常报 Operation not permitted。
# 请给 /usr/sbin/cron（以及建议 /bin/bash）「完整磁盘访问权限」，
# 或改用更稳妥的：./scripts/install_launchd.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 用绝对路径调用 bash，避免相对路径 + TCC 问题加重
LINE="0 1 * * * /bin/bash ${ROOT}/scripts/periodic_run.sh >> ${ROOT}/logs/cron.log 2>&1"

chmod +x "${ROOT}/scripts/periodic_run.sh" \
  "${ROOT}/scripts/weekly_run.sh" \
  "${ROOT}/scripts/daily_run.sh" \
  "${ROOT}/scripts/install_cron.sh"

mkdir -p "${ROOT}/logs"

TMP="$(mktemp)"
(crontab -l 2>/dev/null | grep -v 'money_more' | grep -v 'periodic_run.sh' || true) >"$TMP"
echo "$LINE" >>"$TMP"
crontab "$TMP"
rm -f "$TMP"

echo "已安装 cron："
crontab -l | grep periodic_run || true
echo
echo "说明：cron 每天 01:00 触发；未满 5 天会自动跳过。"
echo
echo "【macOS】若 logs/cron.log 出现 Operation not permitted："
echo "  系统设置 → 隐私与安全性 → 完整磁盘访问权限 → 添加 /usr/sbin/cron 和 /bin/bash"
echo "  或改用: ${ROOT}/scripts/install_launchd.sh"
echo
echo "强制立刻跑一轮：cd ${ROOT} && ./scripts/periodic_run.sh --force"
