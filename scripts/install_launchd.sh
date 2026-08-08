#!/bin/bash
# 安装 macOS LaunchAgent：每周二、周五 01:00 跑一轮分析
# 项目若在 ~/Documents，还必须给 /bin/bash「完整磁盘访问权限」，否则仍会 Operation not permitted。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.money_more.periodic"
DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "${HOME}/Library/LaunchAgents" "${ROOT}/logs"
chmod +x "${ROOT}/scripts/periodic_run.sh" "${ROOT}/scripts/install_launchd.sh" "${ROOT}/scripts/install_cron.sh"

# StartCalendarInterval: 数组 = 多个日历触发点
# launchd Weekday: 0/7=周日, 1=周一, 2=周二, …, 5=周五, 6=周六
cat >"$DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${ROOT}/scripts/periodic_run.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict>
      <key>Weekday</key>
      <integer>2</integer>
      <key>Hour</key>
      <integer>1</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>
    <dict>
      <key>Weekday</key>
      <integer>5</integer>
      <key>Hour</key>
      <integer>1</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>
  </array>
  <key>StandardOutPath</key>
  <string>${ROOT}/logs/cron.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/logs/cron.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
EOF

# 同步一份到仓库 scripts/ 便于版本管理
cp "$DST" "${ROOT}/scripts/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DST"
launchctl enable "gui/$(id -u)/${LABEL}" || true

echo
echo "已加载 LaunchAgent: ${LABEL}"
echo "排期: 每周二、周五 01:00（分析；config schedule.cadence=tue_fri）"
echo "plist: ${DST}"
echo
echo "【重要 · macOS】项目在 Documents 下会被系统拦截。请："
echo "  系统设置 → 隐私与安全性 → 完整磁盘访问权限"
echo "  点 + ，⌘⇧G，输入 /bin/bash ，勾选启用"
echo
echo "查看状态: launchctl print gui/$(id -u)/${LABEL} | head -40"
echo "立刻试跑: launchctl kickstart -k gui/$(id -u)/${LABEL}"
echo "  或: cd ${ROOT} && ./scripts/periodic_run.sh --force"
