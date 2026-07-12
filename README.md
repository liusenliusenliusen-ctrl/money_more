# money_more

A 股 **中长线** AI 研究助手：默认 **每 5 天**、凌晨 **1 点** 跑一轮——分析 / 荐股 / 复盘 → Cursor 自优化 →（可选）邮件送达报告。

仓库：https://github.com/liusenliusenliusen-ctrl/money_more

## 定位

| | 本项目 |
|---|---|
| 投资取向 | 中长线（数周–数季），非短线 |
| 调度 | 每 5 天一次；cron 每天 01:00 触发，脚本按间隔门禁 |
| 产出 | 分析报告、优化报告、趋势报告、纸面统计 |
| 通知 | SMTP 邮件（分析 + 自优化报告） |
| 自进化 | 周期结束后 Cursor 优化代码；优先补数据源（宏观/基本面/交易/舆情），再改分析 |
| 多Agent | 决策：DeepSeek 主分析 + Cursor 副分析 → DeepSeek 综合（可换 Claude） |

## 周期流程

```
money-more scheduled
  ├─ 间隔门禁（未满 5 天则跳过；--force 可强制）
  ├─ 情报（14 日窗口，过滤日内噪声）
  ├─ 市场 / 板块 / 个股（中长线 prompt）
  ├─ 建议（默认 medium/long；质量+估值加权更高）
  ├─ 复盘（建议发出 ≥14 天再打分）
  ├─ 纸面盯市（最长约 60 天）
  ├─ 分析报告 → reports/YYYY-MM-DD.md + 趋势 reports/trend.md
  ├─ （可选）邮件发送分析报告
  ├─ Cursor 自优化 → reports/optimize-YYYY-MM-DD.md
  └─ （可选）邮件发送优化报告
```

严谨性：as_of 贯通、决策硬约束、因子评分卡、双源/硬门禁、纸面统计、周期 digest 对比。

## 快速开始

```bash
cd money_more
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp config.yaml.example config.yaml   # 改关注板块、个股、持仓
cp .env.example .env                 # 填 LLM / Cursor / 邮件等密钥

money-more doctor                    # 环境自检（不调 LLM）
money-more scheduled --force         # 立刻跑一轮（忽略 5 天门禁）
money-more email-test                # 验证邮件（需先配好 SMTP）
```

推荐日常入口：`money-more scheduled`（或 cron 调用 `scripts/periodic_run.sh`）。

## 环境变量（`.env`）

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 分析用大模型（OpenAI 兼容，如 DeepSeek） |
| `TUSHARE_TOKEN` | 可选；公告/财务/估值等增强源 |
| `CURSOR_API_KEY` | 周期自优化 + 多 Agent 副分析师 |
| `ANTHROPIC_API_KEY` / `CLAUDE_*` | 可选；`secondary_provider: claude` 时用 |
| `EMAIL_ENABLED` | `true` 开启邮件 |
| `SMTP_HOST` / `SMTP_PORT` | 如 `smtp.qq.com` / `465` |
| `SMTP_USER` / `SMTP_PASSWORD` | 邮箱账号 + **授权码**（不是登录密码） |
| `EMAIL_FROM` / `EMAIL_TO` | 发件人 / 收件人（可相同） |

`config.yaml`、`.env`、本地 `data/` / `reports/` / `logs/` **不会**进 Git。

## 配置要点（`config.yaml`）

| 项 | 默认含义 |
|----|----------|
| `schedule.cadence` / `interval_days` | `every_5_days` / `5` |
| `schedule.run_hour` | 文档约定凌晨 1 点（真正几点由 crontab 决定） |
| `schedule.optimize_after_run` | 周期流程默认跑完后自优化 |
| `analysis.investment_horizon` | `medium_long` |
| `trading.stop_loss_pct` / `take_profit_pct` | `15` / `40` |
| `optimize.skip_if_dirty` | 有未提交代码改动则跳过自优化 |
| `optimize.respect_human_lock` | 存在 `logs/OPTIMIZE_PAUSE` 则跳过 |
| `email.send_analysis` / `send_optimize` | 是否分别发两类报告邮件 |
| `agents.decision_multi` | 决策环节双分析 + 综合（默认 true） |
| `agents.secondary_provider` | `cursor` / `claude` / `none` |
| `agents.synthesizer_provider` | 默认 `deepseek`（推荐） |

## 多 Agent 决策

| 角色 | 默认 | 为什么 |
|------|------|--------|
| 主分析师 | DeepSeek | 日常链路；结构化 JSON 稳 |
| 副分析师 | Cursor | 独立二意见，可读 reports/ 上下文 |
| 综合委员 | **DeepSeek** | 便宜、schema 稳；Cursor 更适合当分析师 |

```yaml
agents:
  enabled: true
  decision_multi: true
  primary_provider: deepseek
  secondary_provider: cursor   # 或 claude / none
  synthesizer_provider: deepseek
```

副分析不可用时自动回退单 DeepSeek。

## 邮件通知

报告生成后自动发信：正文为 Markdown 预览，完整文件作附件。

```bash
# QQ 邮箱示例（密码填授权码）
EMAIL_ENABLED=true
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=你的QQ邮箱
SMTP_PASSWORD=授权码
EMAIL_FROM=你的QQ邮箱
EMAIL_TO=你的收件邮箱

money-more email-test
```

QQ 授权码获取（新版界面）：网页版 mail.qq.com → 右上角头像 → **设置** → **账号与安全** → **安全设置** → 开启服务 → **生成授权码**。  
说明：https://help.mail.qq.com/detail/0/985

## 定时运行

每天 **01:00** 触发；脚本内按 `interval_days=5` 门禁，未到间隔则跳过。

### macOS 必读（`Operation not permitted`）

项目在 `~/Documents` 下时，系统会拦截 **cron / 后台 bash** 访问该目录，于是：

- `logs/cron.log` 出现：`./scripts/periodic_run.sh: Operation not permitted`
- **不会**生成报告，也 **不会**发邮件

处理步骤：

1. **完整磁盘访问权限**（必需）  
   系统设置 → 隐私与安全性 → **完整磁盘访问权限** → `+` → `⌘⇧G` → 添加并勾选：
   - `/bin/bash`
   - `/usr/sbin/cron`（若继续用 crontab）
2. **改用 LaunchAgent**（推荐）  
   ```bash
   chmod +x scripts/install_launchd.sh
   ./scripts/install_launchd.sh
   ```

也可把仓库挪出 `Documents`（例如 `~/code/money_more`），减少拦截。

### 安装定时任务

```bash
# 推荐（macOS）
./scripts/install_launchd.sh

# 或 crontab
./scripts/install_cron.sh
# 0 1 * * * /bin/bash /path/to/money_more/scripts/periodic_run.sh >> .../logs/cron.log 2>&1
```

清空 `logs/last_full_run.txt` 后，下一次 01:00 会当作首次完整运行。手动补跑：

```bash
./scripts/periodic_run.sh --force
```

## 人工改动 vs 自优化

可用 Cursor CLI / IDE 手工改代码；为防与周期自优化冲突：

1. 动手前：`touch logs/OPTIMIZE_PAUSE`
2. 改完后：`rm logs/OPTIMIZE_PAUSE`
3. 默认工作区有未提交代码改动时也会跳过自优化
4. 自优化只追加 `logs/optimization_progress.txt`，禁止 reset / 覆盖人工改动

## 信息源

| 层级 | 来源 | 内容 |
|------|------|------|
| 宏观 | 新闻联播、全球财经、经济日历、财联社、RSS、Tushare | 政策与事件 |
| 资金 | 北向、两融、板块净流入 | 资金面 |
| 情绪 | 东财人气、雪球热度、舆情打分 | 拥挤度 |
| 板块 | 同花顺排名、板块新闻 | 轮动与叙事 |
| 个股 | 新闻/研报/龙虎榜、Tushare 公告财报估值 | 基本面与预期差 |

Tushare 部分接口需 Pro 权限；未配置 Token 时自动跳过。

## 分析框架

LLM 按九层综合：宏观政策 → 资金面 → 基本面 → 技术面 → 舆情情绪 → 叙事预期 → 交叉验证 → 主要矛盾 → 失效条件。

## 命令

| 命令 | 说明 |
|------|------|
| `money-more scheduled` | 周期：门禁 → 分析 →（默认）自优化 → 邮件 |
| `money-more scheduled --force` | 忽略间隔，强制跑一轮 |
| `money-more scheduled --skip-optimize` | 只分析（仍可发分析邮件） |
| `money-more weekly` | 同 `scheduled`（兼容旧名） |
| `money-more run` | 完整分析；加 `--optimize` 才自优化 |
| `money-more optimize` | 仅 Cursor 自优化 |
| `money-more email-test` | 发送测试邮件 |
| `money-more review` | 仅复盘 |
| `money-more lessons` / `history` | 经验库 / 历史建议 |
| `money-more trend` | 滚动趋势报告 |
| `money-more stats` | 纸面交易统计 |
| `money-more doctor` | 环境与数据源自检 |
| `money-more risk-check` | 仓位/板块集中度 |
| `money-more compare` | 近日 digest 稳定性对比 |

## 数据与报告

| 路径 | 说明 |
|------|------|
| `data/money_more.db` | SQLite |
| `reports/YYYY-MM-DD.md` | 分析报告 |
| `reports/optimize-YYYY-MM-DD.md` | 自优化报告 |
| `reports/trend.md` | 趋势报告 |
| `logs/last_full_run.txt` | 上次成功周期日期（门禁用） |
| `logs/OPTIMIZE_PAUSE` | 人工改码暂停自优化 |
| `logs/cron.log` | cron 输出 |

## 免责声明

本工具由 AI 生成分析内容，仅供学习与研究，不构成任何投资建议。投资有风险，决策与后果由使用者自行承担。
