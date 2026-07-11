#!/bin/bash
# 每 5 天周期任务：分析报告 + Cursor 自优化报告
# cron 每天凌晨 1 点触发；脚本内按 interval_days=5 门禁，未到间隔则跳过。
#
# crontab 示例（每天 01:00）：
# 0 1 * * * cd /Users/liusen/Documents/money_more && ./scripts/periodic_run.sh >> logs/cron.log 2>&1

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p logs data reports

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export PYTHONPATH="${ROOT}/src:${PYTHONPATH:-}"

# 周期：门禁 → 分析 + Cursor 自优化（可用 --force 强制；--skip-optimize 只分析）
python -m money_more scheduled "$@"
