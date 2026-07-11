#!/bin/bash
# 兼容旧 cron：转发到周期脚本
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/periodic_run.sh" "$@"
