#!/bin/bash
# 周二/周五 01:00 周期任务：分析报告（可发邮件）
# 由 LaunchAgent / cron 在排期日触发；门禁见 schedule.cadence=tue_fri
#
# crontab 示例（周二、周五 01:00）：
# 0 1 * * 2,5 cd /path/to/money_more && ./scripts/periodic_run.sh >> logs/cron.log 2>&1

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p logs data reports

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export PYTHONPATH="${ROOT}/src:${PYTHONPATH:-}"

# 周期：门禁 → 分析（可用 --force 强制）
python -m money_more scheduled "$@"
