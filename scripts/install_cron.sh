#!/bin/bash
# 安装本机 cron：每周二、周五 01:00 触发分析（门禁 cadence=tue_fri）
#
# macOS 注意：项目在 ~/Documents 时，cron 常报 Operation not permitted。
# 请给 /usr/sbin/cron（以及建议 /bin/bash）「完整磁盘访问权限」，
# 或改用更稳妥的：./scripts/install_launchd.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# cron Dow: 0=周日, 1=周一, 2=周二, …, 5=周五
LINE="0 1 * * 2,5 /bin/bash ${ROOT}/scripts/periodic_run.sh >> ${ROOT}/logs/cron.log 2>&1"

mkdir -p "${ROOT}/logs"
chmod +x "${ROOT}/scripts/periodic_run.sh" \
  "${ROOT}/scripts/install_cron.sh"

TMP="$(mktemp)"
(crontab -l 2>/dev/null | grep -v 'money_more' | grep -v 'periodic_run.sh' || true) >"$TMP"
echo "$LINE" >>"$TMP"
crontab "$TMP"
rm -f "$TMP"

echo "已安装 cron："
crontab -l | grep periodic_run || true
echo
echo "说明：每周二、周五 01:00 触发；非排期日若被误触也会被 cadence=tue_fri 门禁跳过。"
echo
echo "【macOS】若 logs/cron.log 出现 Operation not permitted："
echo "  系统设置 → 隐私与安全性 → 完整磁盘访问权限 → 添加 /usr/sbin/cron 和 /bin/bash"
echo "  或改用: ${ROOT}/scripts/install_launchd.sh"
