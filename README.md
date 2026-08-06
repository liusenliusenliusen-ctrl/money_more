# money_more

A 股 **中长线** AI 研究助手：需要时 **手动** 跑一轮——分析 / 建议 / 复盘 →（可选）Cursor 自优化 →（可选）邮件送达报告。

仓库：https://github.com/liusenliusenliusen-ctrl/money_more

## 定位

| | 本项目 |
|---|---|
| 投资取向 | 中长线（数周–数季），非短线 |
| 调度 | **每周二、周五 01:00** 自动跑一轮（含自优化）；也可手动 `--force` |
| 个股遴选 | 默认全 A 现货漏斗 → 量化池 → 深度池（**自动筛选**；无独立「必跟股池」） |
| 持仓 | 仅认 `config.yaml` 的 `holdings`；**未声明 = 空仓**；有持仓则强制进深度池 |
| 产出 | 主报告（运行状态→结论卡→详细论证→D 趋势）+ 数据源/复盘/模拟小报告、优化报告、滚动趋势 |
| 通知 | SMTP：正文=结论卡；附件=主报告 md |
| 自进化 | 周期结束后 Cursor 优化代码；优先补数据源，再改分析 |
| 多Agent | DeepSeek 主分析 + Cursor 副分析 → DeepSeek 综合（可换 Claude） |

## 周期流程

```
money-more scheduled
  ├─ 排期门禁（cadence=tue_fri：仅周二/周五；--force 可强制）
  ├─ 情报（14 日窗口）
  ├─ 市场 / 板块（watch_sectors + 资金流自动扩 → 结论卡 B1）/ 个股漏斗
  ├─ 决策链：①研究 → ②组合草案（双分析师 → 综合）→ ③辩论（凡 buy/add）→ ④风控终局
  ├─ 复盘（近 60 日；浮盈亏 ≠ 结案）→ 独立 *-review.md
  ├─ 模拟账本（回放④终局）→ 独立 *-sim.md
  ├─ 报告 → 主报告.md + *-datasources.md + *-review.md + *-sim.md；趋势 reports/trend.md
  ├─ （可选）邮件：正文结论卡 · 附件仅主报告
  ├─ （可选）Cursor 自优化
  └─ （可选）邮件发送优化报告
```

严谨性：as_of 贯通、空仓/深度池硬约束、遴选失败进数据质量、因子评分卡、双源/硬门禁、模拟账本与真实持仓分离。  
全面性补充：**叙事雷达** + A1 争议/未验证假说；**政策市假说**；**微观结构/流动性断点**；个股**信息完备性**（缺口→观望）；报告 **A 主结论（A1 含主线与争议） / B 推理链 / D 趋势**。

读报：[`docs/how-to-read-report.md`](docs/how-to-read-report.md)（首次邮件会附带）。  
数据说明书（比 `*-datasources.md` 更细）：[`docs/data-sources-guide.md`](docs/data-sources-guide.md)。  
数据接口目录（精确到接口名）：[`docs/data-interfaces-catalog.md`](docs/data-interfaces-catalog.md)。  
数据语义（本质·用途·禁区）：[`docs/data-semantics-guide.md`](docs/data-semantics-guide.md)。

## 快速开始

```bash
cd money_more
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp config.yaml.example config.yaml   # 先读清 holdings / screen
cp .env.example .env                 # 填 LLM / Cursor / 邮件等密钥

money-more doctor                    # 环境自检（会提示空仓语义）
money-more scheduled --force --skip-optimize   # 立刻跑一轮分析
money-more email-test                # 验证邮件（需先配好 SMTP）
```

推荐入口：定时任务自动跑；需要时也可手动 `money-more scheduled --force`。  
**跑任务时**：说明了持仓就写入 `holdings`；未提持仓 → 清空为 `holdings: []` 再跑。

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
| `EMAIL_FROM` / `EMAIL_TO` | 发件人 / 收件人；`EMAIL_TO` 可多个（逗号或分号分隔） |

`config.yaml`、`.env`、本地 `data/` / `reports/` / `logs/` **不会**进 Git。

## 配置要点（`config.yaml`）

| 项 | 默认含义 |
|----|----------|
| `holdings` | 真实持仓；`[]` = 空仓（有仓则强制进深度池） |
| `watch_sectors` | 板块叙事关注（≠个股必跟池） |
| `screen.universe_mode` | 默认 `spot_all`（全 A 现货）；`sector_spot` 为窄池 |
| `screen.max_quant` / `max_deep` | `50` / `15`（持仓强制进池时**不占** `max_deep`） |
| `screen.pe_max` / `exclude_negative_pe` | 默认 `0` / `false`（高 PE、负 PE 不硬剔，打分降权） |
| `screen.auto_sector_from_flow` | 资金流入前列自动扩 B1 板块（默认 3） |
| `analysis.debate_top_k` | `>0` 开启：所有 `buy`/`add` 必辩；`0` 关闭（如 `--skip-debate`） |
| `schedule.cadence` | 默认 `tue_fri`（每周二/周五）；也可用 `every_5_days` |
| `schedule.interval_days` | 非 `tue_fri` 时的间隔门禁天数 |
| `schedule.run_hour` | 定时触发小时（默认 1=凌晨） |
| `schedule.optimize_after_run` | `scheduled` 默认跑完后自优化 |
| `trading.stop_loss_pct` / `take_profit_pct` | `15` / `40` |
| `sim.*` | 独立小报告 `*-sim.md`；缺 `position_pct` 不静默开仓 |

## 术语（勿混用）

| 术语 | 含义 |
|------|------|
| 声明持仓 | `holdings` |
| 量化池 | 漏斗打分入围（`max_quant`） |
| 深度池 | 本轮 LLM 细读名单（量化前列 ∪ 声明持仓） |
| ①研究评级 | 单票 `research_rating`；**≠** 开仓 |
| ②组合草案 | 双分析师独立草案 → **综合委员**取舍后的动作/仓位 |
| ④风控终局 | 可执行动作；结论卡 A3 / 模拟盘只认这一层 |
| 模拟账本 | 决策后回放「若完全按④执行」；见 `*-sim.md` |

## 多 Agent 决策

| 角色 | 默认 | 为什么 |
|------|------|--------|
| 主分析师 | DeepSeek | 日常链路；结构化 JSON 稳 |
| 副分析师 | Cursor | 独立二意见；**持仓只认本轮 payload，不从历史报告继承** |
| 综合委员 | **DeepSeek** | 便宜、schema 稳；合并两份草案写入② |

决策链：**①研究 → ②组合草案（主/副 → 综合）→ ③辩论（凡 buy/add）→ ④风控终局**。  
副分析不可用时自动回退单 DeepSeek。结论卡 B2 有「步骤说明」。

## 邮件通知

默认**只发送分析报告**（`email.send_optimize: false`）。

- **正文**：仅结论卡（HTML）
- **附件**：仅主报告 `.md`（含详细论证与 D 趋势）
- **不附**：`*-datasources.md`、`*-review.md`、`*-sim.md`、`trend.md`
- **首次发送**：每个收件人第一次收到邮件时，另附 `docs/how-to-read-report.md`

## 运行方式

**定时（已启用）**：每周二、周五本地 **01:00** 由 LaunchAgent 拉起 `periodic_run.sh`（分析 + 自优化）。  
安装/重装：`./scripts/install_launchd.sh`（macOS 推荐）；或 `./scripts/install_cron.sh`。

手动：

```bash
# 本地
.venv/bin/python -m money_more scheduled --force --skip-optimize   # 只要分析+邮件
.venv/bin/python -m money_more scheduled --force                   # 分析后顺带自优化

# 或
./scripts/periodic_run.sh --force
```

`schedule.cadence=tue_fri` 时，非周二/周五会被门禁跳过（`--force` 除外）。  
项目在 `~/Documents` 下时，请给 `/bin/bash`「完整磁盘访问权限」，否则定时任务可能 `Operation not permitted`。

## 人工改动 vs 自优化

1. 动手前：`touch logs/OPTIMIZE_PAUSE`
2. 改完后：`rm logs/OPTIMIZE_PAUSE`
3. 默认工作区有未提交改动时也会跳过自优化

## 信息源

| 层级 | 来源 | 内容 |
|------|------|------|
| 宏观 | 新闻联播、全球财经、经济日历、财联社、RSS、Tushare | 政策与事件 |
| 全球流动性 | 中美国债收益率、USD/CNY（硬指标 stance） | 外因风险偏好；进主结论 |
| 资金 | 北向、两融、板块净流入 | 资金面；可扩 B1 板块 |
| 情绪 | 东财人气、雪球热度、舆情打分 | 拥挤度 |
| 板块 | 同花顺/东财排名、板块新闻 | 轮动与叙事 |
| 个股 | 全 A 现货漏斗 + 新闻/研报/龙虎榜、Tushare 公告财报估值 | 基本面与**盈利预期修正** |

## 分析框架

LLM 按综合框架：宏观政策 → **全球流动性** → 产业景气 → 基本面/**盈利预期修正** → 估值 → 资金 → 舆情叙事 → 交叉验证 → 主要矛盾 → 失效条件 → 争议/尾部侧栏。

硬约束补充：空仓禁止 hold/sell/add；深度池外禁止 buy/add；遴选失败进数据质量；盈利下修/信息缺口禁止新买；全球流动性收紧时收总仓；未确认网络叙事不得单独驱动买入。

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
| `money-more stats` | 旧纸面台账 + 模拟组合统计 |
| `money-more sim` | 查看模拟组合；`--reset` 清空重来 |
| `money-more doctor` | 环境与数据源自检（含持仓/空仓提示） |
| `money-more risk-check` | 仓位/板块集中度 |
| `money-more compare` | 近日 digest 稳定性对比 |

## 数据与报告

| 路径 | 说明 |
|------|------|
| `data/money_more.db` | SQLite |
| `reports/YYYY-MM-DD.md` | 主报告：运行状态 → 结论卡 A/B/C → 详细论证 A/B/C → D 趋势 |
| `reports/YYYY-MM-DD-datasources.md` | 数据源台账小报告（邮件不附） |
| `reports/YYYY-MM-DD-review.md` | 复盘与经验小报告（邮件不附） |
| `reports/YYYY-MM-DD-sim.md` | 模拟账本小报告（邮件不附） |
| `reports/optimize-YYYY-MM-DD.md` | 自优化报告 |
| `reports/trend.md` | 滚动趋势报告 |
| `logs/last_full_run.txt` | 上次成功周期日期（门禁用） |
| `logs/OPTIMIZE_PAUSE` | 人工改码暂停自优化 |
| `docs/how-to-read-report.md` | 读报指南（首次邮件附件） |

## 免责声明

本工具由 AI 生成分析内容，仅供学习与研究，不构成任何投资建议。投资有风险，决策与后果由使用者自行承担。
