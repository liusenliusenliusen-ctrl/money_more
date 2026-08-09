# money_more 优化方案 · 第三波

> 状态：**代码已落地**（2026-08-08；部署+冒烟留全部完成后；Tushare/Cursor/数据源深治见 TODO）  
> 前置：第一波 / 第二波已落地（见 [`optimization-plan-v2.md`](optimization-plan-v2.md)、[`optimization-plan-wave2.md`](optimization-plan-wave2.md)）  
> 本波主题：**数据通路稳住 + 进池更中长线 + 收尾薄补**（少堆新故事源）  
>  
> **已拍板**：`eastmoney_force_direct=开`；`amount_avg_days=20`；RSS 公网 fallback **默认关**；**不做** Tushare 升积分、Cursor 副分析师（记 TODO）；东财/RSS 深治另记 TODO，本波只落临时策略。

---

## 0. 本波目标与非目标

### 0.1 目标

1. **筛股主通路少挂**：东财 push2 在代理干扰下可强制直连；spot/hist/热榜/个股资金流降级更醒目。  
2. **快讯链不断**：财联社超时可配；早餐/备用链默认可用；RSS 镜像可选手动开。  
3. **进池 P1**：成交额改多日均；`auto_sector_from_flow` 单日只观察、多日/叙事重叠才升权。  
4. **第二波薄补收口**：数据质量完整版进结论卡；景气 `inflection_signal` 可核对豁免。  
5. **Tushare**：若你升级积分，现有调用路径直接吃满（fina/forecast/anns/联播/重大新闻）；本波只做探测与台账诚实，不假装有权限。

### 0.2 非目标（本波不做）

| 项 | 原因 |
|----|------|
| FRED / 居民存款 / 一致预期付费源 | TODO §13；主通路未稳前不堆 |
| 恢复 Cursor 副分析师（9b） | 须先压 payload；本波可单列「可选」，默认不做 |
| 动 ERP / 止损 / 硬门禁宪法 | 不变 |
| 统一冒烟 | 按你要求：全部完成后再测 |

### 0.3 与前两波的关系

- 第一波：更钝、管住手（暴涨剔除、框架门禁、社融）。  
- 第二波：能对答案（验证窗口、sector_link、复盘对照）。  
- 第三波：数据少挂、进池更稳、薄补收口。

---

## 1. 范围清单（本波实施包）

| ID | 项 | 价值 | 验收一句话 |
|----|-----|------|------------|
| W3-1 | **东财 push2 强制直连** | 高 | 配置可关代理干扰；spot 仍失败时结论卡/台账醒目标 sina/stale |
| W3-2 | **EM 调用补强** | 高 | hist 一次重试；个股资金流失败有错误串且不静默；可选二次入口探测 |
| W3-3 | **财联社 / 快讯硬化** | 中高 | `cls_timeout` 可配；早餐链 doctor/台账可见；RSS 仍默认关、URL 可配 |
| W3-4 | **筛股 P1：多日成交额** | 高 | `amount_avg_days`（默认 20）过滤+打分；缺 hist 时降级当日并记 degraded |
| W3-5 | **筛股 P1：auto_sector 确认** | 中高 | 单日流入=观察扩；连续同向或叙事雷达重叠=升权扩；报告区分标签 |
| W3-6 | **数据质量完整版（原 W2-9）** | 中 | 结论卡列出全部 `dq.missing` + 抽样 errors（红字/醒目） |
| W3-7 | **`inflection_signal`（原 W2-10）** | 中 | 研究输出拐点字段；validator 仅在信号为真且有证据时豁免景气禁加仓 |
| W3-8 | **文档 + TODO 勾选** | 工程 | 本文件 + `TODO_optimizations.md` 同步；Tushare/Cursor/数据源深治记入 TODO；**部署与冒烟留到全部完成后** |

### 本波明确不做（已记 TODO）

| 项 | 说明 |
|----|------|
| Tushare **积分升级** | 付费开关；现有路径升级后即可吃满，本波不推动 |
| Cursor 副分析师压缩（9b） | 继续 DeepSeek 双角色；恢复 Cursor 前须压 payload |
| 东财 / RSS **深治专项** | 本波仅临时：`force_direct=开`、RSS 公网 fallback **关**；后续单独好好做数据源获取 |

---

## 2. 详细设计（摘要）

### 2.1 东财 push2（W3-1 / W3-2）

**行为**

- 新增 `data.eastmoney_force_direct`（建议默认 `true`）：在调用 EM/ak 东财系接口前，临时清除/忽略 `HTTP(S)_PROXY`，或对 session `trust_env=False`。  
- 覆盖优先：`stock_zh_a_spot_em`、`stock_zh_a_hist`、热榜、个股资金流、板块资金流（若仍走 EM）。  
- 现有 fallback 保留（sina spot / sina daily / 雪球热榜 / THS 板块）。  
- 失败时 errors 带 `push2`/`ProxyError` 标签；DQ / 结论卡能看出「行情降级」。

**涉及**：`data/fetcher.py`（或小模块 `data/ak_session.py`）、`config.py`、`config.yaml.example`、ledger/DQ。

### 2.2 财联社（W3-3）

**现状**：早餐备用已优先；CLS 8s 线程超时；`use_fallback_rss` 默认 false。

**本波**：`RssConfig` 增加/暴露 `cls_timeout_sec`；可选 `rsshub_base`；doctor 打印快讯源命中。不默认打开公网 rsshub.app（易超时）。

### 2.3 筛股 P1（W3-4 / W3-5）

**成交额**

```yaml
screen:
  amount_avg_days: 20   # 0=关闭，仍用当日额
```

- 对 universe 候选批量取近 N 日额均值（缓存）；过滤 `min_amount` 与打分 amount 维改用均值。  
- 取不到 hist：该票用当日额并记 `amount_avg_fallback`（不因个别票拖死整轮）。

**auto_sector**

- 近 1 日 top 流入：仅标记 `observe`（进关注但不升权 / 或低权）。  
- 升权条件（满足其一）：近 2–3 个交易日同向流入靠前，或与 `narrative_radar` 主题重叠。  
- 报告写清「观察扩 / 升权扩」。

### 2.4 薄补（W3-6 / W3-7）

- **DQ 完整版**：A0 除速览外，展开 `missing` 全列表（可截断超长 errors）。  
- **拐点**：研究/板块 JSON 增 `inflection_signal: bool` + `inflection_evidence`；validator：`prosperity=down` 且非拐点 → 禁 buy/add；拐点为真 → 允许但结论卡标注「景气拐点豁免」。

### 2.5 Tushare（W3-8）

- **你侧**：是否在 tushare.pro 升级积分（付费决策）。  
- **代码侧**：探测脚本/doctor 列出 `fina_indicator` / `forecast` / `anns_d` / `major_news` / `cctv_news` 权限结果；升级后同路径填充；不新增假数据。

---

## 3. 实施顺序

1. W3-1 + W3-2 push2 / EM  
2. W3-3 快讯硬化  
3. W3-4 + W3-5 筛股 P1  
4. W3-6 + W3-7 薄补  
5. W3-8 文档勾选 + TODO（Tushare/Cursor/数据源深治）  
6. **全部完成后**：部署 + 统一冒烟（实施中不冒烟）

---

## 4. 拍板记录（2026-08-08）

| # | 决定 |
|---|------|
| 1 | `eastmoney_force_direct` **默认开**（临时）；深治记 TODO |
| 2 | `amount_avg_days` = **20** |
| 3 | Tushare 升积分 **本波不做** → TODO |
| 4 | Cursor 副分析师 **本波不做** → TODO |
| 5 | RSS 公网 fallback **默认关**（临时）；深治记 TODO |

---

## 5. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 强制直连在必须走代理的网络反而失败 | 配置可关；保留 sina/THS fallback |
| 多日成交额批量 hist 慢/易限流 | 缓存 + 仅对过硬过滤后的候选算；可关 `amount_avg_days: 0` |
| auto_sector 过严导致板块偏少 | 观察扩仍进名单；升权才加权 |
| 拐点豁免被 LLM 滥标 | 须非空 evidence；否则当 false |

---

## 6. 一句话

> **第三波把东财/快讯通路钉稳，进池用多日额与板块确认，再收口数据质量卡与景气拐点豁免；Tushare 升级是你的付费开关，代码路径已备好。冒烟等全部完成后再跑。**

确认 §4 五项后按 §3 改代码。
