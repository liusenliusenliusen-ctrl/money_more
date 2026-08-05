# 待办优化清单

记录讨论中认可、但**暂不实施**的优化项。  
按性价比排序；动手前先勾选范围，避免一次铺太开。

最后更新：2026-08-05

---

## 高价值（直接影响「能不能验证对错」）

### 1. 结论卡可核对化：验证窗口
- **目标**：主情景 / 每条动作旁增加「验证窗口」（如 14/30 天看什么信号），与失效条件并列。
- **用途**：外行事后打钩验证，而不是只看当时文案。
- **涉及**：`report/writer.py` 结论卡；可选决策 JSON 增 `verify_in_days` / `verify_signals` 字段与 prompt。

### 2. 维度复盘自动化对照表
- **目标**：复盘时先用代码算「当时 phase/style/板块优先级 vs 后来」差异表，再让 LLM 解释。
- **用途**：少空话、可回测；复盘从「写感想」变成「对答案」。
- **涉及**：`review_history.py` / `run_review`；报告 §5 增加对照表；LLM 只吃结构化 diff。

### 3. 板块→个股映射补全与显式缺标的
- **目标**：板块 high 但 watchlist 无对应标的时，结论卡写清「缺标的 / 仅约束风格」，避免误以为漏推。
- **涉及**：`sector_map.py` 扩展；结论卡逻辑链；可选 config 提示。

---

## 中价值（分析质量）

### 4. 决策强制引用逻辑链（sector_link）
- **目标**：每条建议带 `sector_link`：承接哪条板块结论、为何从研究 buy 降为 watch/hold。
- **涉及**：`DECISION_SYSTEM`、校验、§4 渲染。

### 5. 多 Agent 分歧外显
- **目标**：结论卡一行写清主/副一致点与分歧点（现有草稿未充分展示）。
- **涉及**：orchestrator 元数据；`writer` 结论卡；可选存入 digest。

### 6. 数据质量进结论卡
- **目标**：`degraded` / 缺失源（如 `sector_money_flow`）直接出现在速读区，降低误读置信度。
- **涉及**：`render_conclusion_card`。

---

## 工程与节奏

### 7. 服务器同步部署
- **目标**：本地报告模板 / 复盘逻辑与 `root@118.196.122.208:/opt/money_more` 保持一致，避免下一轮仍跑旧逻辑。
- **涉及**：rsync/tar 部署流程；可选 `scripts/deploy_server.sh`。

### 8. 历史 digest 离线回填
- **目标**：旧 `reports/digests/*.json` 字段偏瘦，维度复盘前几周偏虚；用已有 `reports/*.md`（及 DB）回填 phase 标签、板块、叙事等。
- **涉及**：一次性脚本；`decision_digest` 字段对齐。

### 9. Token / 成本收紧
- **目标**：在效果不明显下降的前提下省调用：低优先级板块缩写、维持「仅决策多 Agent」等。
- **涉及**：pipeline 分支；`agents` / `analysis` 配置。

### 9b. Cursor SDK 作副分析师：大 payload 挂死（2026-08 记下）
- **现象**：`secondary_provider: cursor` 时，决策阶段把完整 payload（可达数 MB）塞进 `Agent.prompt`；本地 `cursor-sdk-bridge` 易长时间 `poll` 不返回。旧超时封装 `ThreadPoolExecutor(wait=True)` + 编排 `as_completed` 无总超时，会导致整轮决策拖死（超时侧已部分修复：`timeout_util` `wait=False`、编排 `wait_futures`+deadline）。
- **现状（临时）**：主/副均用同一 DeepSeek（`llm_model`），靠 `DECISION_SYSTEM` vs `DECISION_SECONDARY_SYSTEM`（风控/唱反调）拉开角度；Cursor 不作决策 secondary。
- **后续专项（恢复 Cursor 前必做）**：
  1. 决策 payload **压缩/摘要**后再交给 Cursor（勿整包 dump）；
  2. 超时后主动杀掉 bridge 子进程，避免残留；
  3. 评估 cloud runtime vs local；
  4. 再决定是否把 `secondary_provider` 改回 `cursor`。
- **涉及**：`cursor_provider.py`、`orchestrator.py`、`timeout_util.py`、决策 payload 裁剪。

---

## 数据源连通与补齐（2026-07-19 探测后记下，稍后做）

背景：主链路（新浪行情、同花顺板块、宏观 PMI/CPI/M2、北向、情绪等）大半能通；报告曾因筛股覆盖 + Tushare 权限 **DEGRADED**。  
下列按「先修关键路径，再考虑新源」排，**暂不实施**。

### 10. 东财 `push2` 不稳（筛股主缺口）
- **现象**：`stock_zh_a_spot_em` / 日K hist / 热榜 / 部分资金流 → `push2.eastmoney.com` ProxyError 或连接被掐。
- **影响**：全 A 快照与筛股覆盖不足（有新浪日K、同花顺板块兜底，但 universe 质量掉）。
- **待查**：本机/系统代理是否干扰；是否需强制直连或换镜像入口；筛股在 spot 失败时的降级策略是否够醒目。

### 11. Tushare 权限与频次
- **无权限**：`major_news` / `cctv_news` / `fina_indicator` / `forecast` / `anns_d`（盈利修正、双源交叉、公告增强受影响）。
- **频次极低**：`trade_cal` / `daily_basic` / `share_float` / `stock_company` 等约 1 次/小时即超限。
- **待办**：评估积分升级是否划算；或收紧调用（缓存、按需、探测勿刷额度）。

### 12. 财联社电报路径
- **现象**：`stock_info_global_cls` 易挂起；RSSHub `rsshub.app` ConnectTimeout；配置默认未开 fallback RSS。
- **待办**：缩短/强化超时；可选自建或可用 RSS 镜像；保证早餐/东财全球等备用链始终可用。

### 13. 可选增强源（中长线，非短期堆料）
仅在 10–12 稳住后再考虑：
- 社融 / 信贷 / 居民存款（流动性）
- 一致预期 / EPS 修订（付费源或更高积分）
- FRED / 美联储流动性（全球流动性主信号补强）
- 股东增减持、解禁完整表（供给冲击）

---

## 明确不优先（除非需求变化）

- 在关键源未修好前，再堆一堆短期舆情/题材源（额度紧、收益递减）
- 短期点位 / 涨跌百分比预测（与中长线定位冲突）
- 为报告单独做复杂 UI（当前「结论卡 + 详细论证」markdown 已够用）

---

## 建议的下手顺序（以后做时）

1. **验证窗口 + 维度复盘对照表**（高价值 1–2）  
2. **缺标的明示 + sector_link**（高价值 3 + 中价值 4）  
3. **部署同步 + digest 回填**（工程 7–8）  
4. **数据源：东财 push2 → Tushare 权限/缓存 → 财联社备用**（10–12）  
5. 其余按需

勾选示例：

- [ ] 1 验证窗口  
- [ ] 2 维度复盘对照表  
- [ ] 3 缺标的明示  
- [ ] 4 sector_link  
- [ ] 5 多 Agent 分歧外显  
- [ ] 6 数据质量进结论卡  
- [ ] 7 服务器同步部署  
- [ ] 8 digest 回填  
- [ ] 9 Token 收紧  
- [ ] 10 东财 push2 / 筛股降级  
- [ ] 11 Tushare 权限与调用收紧  
- [ ] 12 财联社电报备用链  
- [ ] 13 可选宏观/一致预期增强源  

