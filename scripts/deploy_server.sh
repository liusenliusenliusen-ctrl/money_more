#!/usr/bin/env bash
# 同步本地 money_more 到服务器 /opt/money_more（第二波工程项）。
# 用法: scripts/deploy_server.sh [--smoke]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${MONEY_MORE_HOST:-root@118.196.122.208}"
REMOTE="${MONEY_MORE_REMOTE:-/opt/money_more}"
SMOKE=0
for a in "$@"; do
  [[ "$a" == "--smoke" ]] && SMOKE=1
done

echo "==> rsync → ${HOST}:${REMOTE}"
rsync -az --delete \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.git/' \
  --exclude 'data/*.db' \
  --exclude 'data/*.db-*' \
  --exclude '.env' \
  --exclude 'config.yaml' \
  --exclude 'logs/' \
  --exclude 'reports/' \
  "${ROOT}/" "${HOST}:${REMOTE}/"

echo "==> ensure venv deps (if requirements.txt present)"
ssh -o BatchMode=yes "${HOST}" "cd ${REMOTE} && if [[ -f requirements.txt && -x .venv/bin/pip ]]; then .venv/bin/pip install -q -r requirements.txt; fi"

if [[ "${SMOKE}" -eq 1 ]]; then
  echo "==> smoke: scheduled --force (background log)"
  ssh -o BatchMode=yes "${HOST}" \
    "cd ${REMOTE} && mkdir -p logs && PYTHONPATH=${REMOTE}/src nohup ${REMOTE}/.venv/bin/python -m money_more scheduled --force > ${REMOTE}/logs/deploy_smoke.log 2>&1 & echo started_pid=\$!"
  echo "tail remote log: ssh ${HOST} 'tail -f ${REMOTE}/logs/deploy_smoke.log'"
else
  echo "==> deploy done (no smoke). Re-run with --smoke to force a round."
fi
