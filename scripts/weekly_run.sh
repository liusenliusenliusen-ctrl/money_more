#!/bin/bash
# 兼容旧名：转发到 periodic_run.sh（每 5 天周期）
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/periodic_run.sh" "$@"
