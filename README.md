# money_more

A 股 **中长线** AI 研究助手：默认 **每 5 天** 跑一次 → 分析/荐股/复盘 → 调用 Cursor Agent 自优化并写报告。

## 定位

| | 本项目 |
|---|---|
| 投资取向 | 中长线（数周–数季），非短线 |
| 调度 | 每 5 天一次，凌晨 1 点（省 token） |
| 产出 | 分析报告 + 优化报告 + 趋势报告 + 纸面统计 |
| 自进化 | 周期跑完后用 Cursor API 改代码并落盘优化报告 |

## 周期流程

```
money-more scheduled
  ├─ 间隔门禁（未满 5 天则跳过；--force 可强制）
  ├─ 情报（14 日窗口，过滤日内噪声）
  ├─ 市场 / 板块 / 个股（中长线 prompt）
  ├─ 建议（默认 medium/long；质量+估值加权更高）
  ├─ 复盘（建议发出 ≥14 天再打分）
  ├─ 纸面盯市（最长约 60 天）
  ├─ 分析报告 reports/YYYY-MM-DD.md + 趋势 reports/trend.md
  └─ Cursor 自优化 → reports/optimize-YYYY-MM-DD.md（需 CURSOR_API_KEY）
```

### 滚动趋势报告

- `reports/trend.md` / `money-more trend`

### 严谨性增强

- as_of 贯通、决策硬约束、因子评分卡、双源/硬门禁、纸面统计、周期 digest 对比
## 信息源（自动采集）

| 层级 | 来源 | 内容 |
|------|------|------|
| 宏观 | 新闻联播、全球财经、经济日历、**财联社电报**、**RSS 快讯** | 政策风向、事件催化 |
| 宏观 | **Tushare Pro** | 宏观新闻、财经要闻 |
| 资金 | 北向汇总、两融趋势、板块净流入 | 资金面验证 |
| 情绪 | 东财人气榜、雪球关注/成交热度、**量化舆情打分** | 拥挤度与情绪温度 |
| 板块 | 同花顺板块排名、板块新闻、RSS/Tushare 匹配 | 轮动与叙事 |
| 个股 | 个股新闻、研报、千股千评、龙虎榜、**Tushare 公告/财报/PE/PB** | 预期差与硬事实 |

### 新增能力

1. **Tushare Pro**：公告 `anns_d`、利润表/资产负债表/现金流、财务指标、每日估值 `daily_basic`、个股/宏观新闻
2. **RSS / 财联社**：直连财联社 API + RSSHub 多分类源（电报/加红/公告/解读/深度）
3. **舆情打分模型**：财经词典 + 事件规则 + 否定/强度修饰，输出 0-100 分及 positive/negative 标签

## 配置

**.env** 新增：

```env
TUSHARE_TOKEN=你的tushare_token
```

**config.yaml** 新增：

```yaml
tushare:
  enabled: true
rss:
  enabled: true
  cls_direct: true
sentiment:
  enabled: true
```

> Tushare 部分接口（公告、新闻）需 Pro 权限；未配置 Token 时自动跳过，不影响其他源。

## 分析框架

LLM 按 **九层综合分析法** 输出结论：宏观政策 → 资金面 → 基本面 → 技术面 → 舆情情绪 → 叙事预期 → 交叉验证 → 主要矛盾 → 失效条件。

## 快速开始

```bash
cd money_more
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp config.yaml.example config.yaml   # 改关注板块、个股、持仓
cp .env.example .env                 # 填 LLM API Key

# 完整分析（不加优化，除非 --optimize）
money-more run

# 周期流程（推荐）：门禁 + 分析报告 + Cursor 优化报告
money-more scheduled

# 强制跑一轮（忽略 5 天门禁）
money-more scheduled --force

# 只分析、不调用 Cursor 优化
money-more scheduled --skip-optimize

# 仅 Cursor 自优化
money-more optimize

# 仅复盘
money-more review

# 查看经验库 / 历史建议
money-more lessons
money-more history
```

## 配置说明

**config.yaml**

- `schedule.cadence`: `every_5_days`（默认）
- `schedule.interval_days`: `5`
- `schedule.run_hour`: `1`（凌晨 1 点，由 cron 触发）
- `analysis.investment_horizon`: `medium_long`
- `trading`：中长线默认止损 15%、止盈 40%
- `optimize.model`: Cursor Agent 模型（默认 `composer-2.5`）
- `optimize.skip_if_dirty`: 有未提交代码改动则跳过自优化（默认 true）
- `optimize.respect_human_lock`: 存在 `logs/OPTIMIZE_PAUSE` 则跳过（默认 true）

**.env**

- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`：分析用大模型
- `TUSHARE_TOKEN`：可选，增强基本面
- `CURSOR_API_KEY`：周期自优化（Dashboard → API Keys）
- 邮件：`EMAIL_ENABLED` / `SMTP_*` / `EMAIL_FROM` / `EMAIL_TO`（见下方）

## 邮件通知

分析报告、自优化报告生成后可自动发到邮箱（正文预览 + Markdown 附件）。

```bash
# .env 示例（QQ 邮箱用授权码）
EMAIL_ENABLED=true
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=你的QQ邮箱
SMTP_PASSWORD=授权码
EMAIL_FROM=你的QQ邮箱
EMAIL_TO=你的收件邮箱

# 验证
money-more email-test
```

`config.yaml` 中 `email.enabled` / `send_analysis` / `send_optimize` 也可控制开关。

## 人工改动 vs 自优化（防冲突）

你也可以用 **Cursor CLI / IDE** 改代码。为避免和周期自优化打架：

1. 开始手工改之前：`touch logs/OPTIMIZE_PAUSE`（有此文件则自优化直接跳过）
2. 改完删掉：`rm logs/OPTIMIZE_PAUSE`
3. 默认若工作区有**未提交的代码改动**，自优化也会跳过（`skip_if_dirty: true`）
4. 自优化 prompt 会提醒：禁止 reset/覆盖人工改动，只追加 `logs/optimization_progress.txt`

## 定时运行（每 5 天 · 凌晨 1 点）

cron **每天** 01:00 触发；脚本按 `interval_days=5` 门禁，未到间隔会自动跳过。

```bash
chmod +x scripts/periodic_run.sh scripts/install_cron.sh

# 一键安装本机 cron（每天 01:00 触发 + 5 天门禁）
./scripts/install_cron.sh

# 或手动：
crontab -e
# 0 1 * * * cd /path/to/money_more && ./scripts/periodic_run.sh >> logs/cron.log 2>&1
```

## 命令

| 命令 | 说明 |
|------|------|
| `money-more scheduled` | 周期：门禁 → 分析报告 + Cursor 优化报告 |
| `money-more scheduled --force` | 忽略间隔，强制跑一轮 |
| `money-more scheduled --skip-optimize` | 只分析 |
| `money-more weekly` | 同 `scheduled`（兼容旧名） |
| `money-more run` | 完整分析（不加优化，除非 `--optimize`） |
| `money-more optimize` | 仅 Cursor 自优化 |
| `money-more email-test` | 发送测试邮件（验证 SMTP） |
| `money-more review` | 仅复盘 |
| `money-more lessons` | 经验库 |
| `money-more history` | 近期建议 |
| `money-more trend` | 滚动趋势报告 |
| `money-more stats` | 纸面交易胜率/收益 |
| `money-more doctor` | 环境自检 |
| `money-more risk-check` | 仓位/板块集中度 |
| `money-more compare` | 近日 digest 稳定性对比 |
## 数据与报告

- 数据库：`data/money_more.db`
- 分析报告：`reports/YYYY-MM-DD.md` 与 `.json`
- 优化报告：`reports/optimize-YYYY-MM-DD.md`
- 上次成功周期：`logs/last_full_run.txt`

## 免责声明

本工具由 AI 生成分析内容，仅供学习与研究，不构成任何投资建议。投资有风险，决策与后果由使用者自行承担。
