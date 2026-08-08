# money_more 优化方案 · 第二波

> 状态：**已落地代码**（2026-08-08）；部署可用 `scripts/deploy_server.sh [--smoke]`  
> 日期：2026-08-08  
> 前置：第一波已落地（见 [`optimization-plan-v2.md`](optimization-plan-v2.md)）  
> 本波主题：**可验证 + 逻辑链外显 + 复盘对答案**（少改数据源，多改「人能不能核对、复盘能不能打钩」）

---

## 0. 本波目标与非目标

### 0.1 目标

1. **动作可核对**：每条主情景/建议带验证窗口（14/30 日看什么），外行能事后打钩。  
2. **逻辑链不断**：建议必须挂上板块承接（`sector_link`）；板块 high 但池内无票时写清「缺标的」。  
3. **复盘变对答案**：代码先算 phase/style/板块 diff 表，再让 LLM 解释；忽略近 5 日噪声。  
4. **分歧可见**：多 Agent 一致/分歧进结论卡一行。  
5. **工程底座**：digest 回填（给对照表喂料）+ 服务器同步到含第一波+本波的代码。

### 0.2 非目标（留给第三波及以后）

| 项 | 原因 |
|----|------|
| 东财 push2 根治 | 偏运维/网络，不阻塞「可验证」 |
| Tushare **积分升级** | 已记 TODO；评估后再做 |
| 新宏观源（居民存款/FRED/一致预期） | 第一波刚加社融，先观察 |
| 草案 P1：成交额多日均、`auto_sector` 确认后扩 | 进池微调，非本波主线 |
| 恢复 Cursor 副分析师 | 须先压缩 payload（TODO 9b） |
| 动 ERP/止损/硬门禁宪法 | 不变 |

### 0.3 与第一波的关系

第一波让系统「更钝、更能管住手」；第二波让外行和复盘「能对答案」。  
第一波已做的数据质量速览（结论卡 A0）本波只做补强，不重做。

---

## 1. 范围清单（本波实施包）

| ID | 项 | 价值 | 验收一句话 |
|----|-----|------|------------|
| W2-1 | **验证窗口** | 高 | A2/A3 旁可见 `verify_in_days` + `verify_signals` |
| W2-2 | **维度复盘对照表** | 高 | `*-review.md` 先出结构化 diff，再 LLM 解释 |
| W2-3 | **复盘去 5 日噪声** | 高（随 W2-2） | REVIEW prompt + 对照口径：看 60 日位置/基本面匹配，不评 5 日浮亏 |
| W2-4 | **缺标的明示** | 高 | 板块 high 且深度池无映射 → 结论卡写「缺标的/仅约束风格」 |
| W2-5 | **sector_link** | 中高 | 每条 buy/add/hold/sell/watch 带承接板块 + 研究→动作降级理由 |
| W2-6 | **多 Agent 分歧外显** | 中 | 结论卡一行：一致点 / 分歧点（有 multi_agent 时） |
| W2-7 | **digest 回填** | 工程 | 旧 digest 补 phase/style/板块/叙事字段，对照表前几周不空 |
| W2-8 | **服务器同步** | 工程 | `/opt/money_more` 与本地第一波+本波一致；冒烟一轮 |

可选薄补（有余力）：

- W2-9：数据质量进结论卡「完整版」（按 missing 列表逐条红字；第一波已有速览）  
- W2-10：景气拐点显式字段 `inflection_signal`（第一波景气禁加仓的豁免可核对化）

---

## 2. 详细设计

### 2.1 验证窗口（W2-1）

**行为**

- A2 主情景：增加「验证窗口」块（默认 14 日 / 30 日两档或一档主窗口）。  
- A3 每条动作：与 `invalidation` 并列，增加：
  - `verify_in_days`: int（如 14 / 30）  
  - `verify_signals`: string[]（可观察、可打钩，如「板块资金连续两周净流入」「PMI 回升至 ≥50」）

**Prompt**

- `ADVICE_SYSTEM` / 市场 A2 相关：强制输出上述字段；信号须可核对，禁止空话（「情绪好转」→ 须改成可观测代理）。

**校验**

- `decision_validator` 或轻量 schema：`buy`/`add` 缺 `verify_signals` 时补默认（如「持有期满 14 日未触发 invalidation」）并记 override，或打回为 watch（建议：**补默认 + override 提示**，避免过严空仓）。

**报告**

- `render_conclusion_card`：A2、A3 动作行渲染验证窗口。  
- 详细论证同步一行即可。

**涉及**：`llm/prompts.py`、`decision_validator.py`、`report/writer.py`；可选 `decision_digest` 持久化字段。

---

### 2.2 维度复盘对照表 + 去短线噪声（W2-2 / W2-3）

**行为**

1. 代码从历史 digest / DB / 报告 JSON 提取：
   - 当时：`phase` / `style` / `risk_level` / 板块 `priority`+`prosperity` / 主叙事标签  
   - 后来（窗口末或今）：同类字段 + 指数/持仓轨迹（轨迹只作旁注）  
2. 生成 **结构化 diff 表**（correct / drifted / reversed / unknown）。  
3. LLM 复盘 **只解释 diff**，禁止脱离表格空谈。  
4. Prompt 硬约束：
   - **忽略近 5 日价格噪声**；  
   - 评估「价格相对约 60 日位置 vs 基本面/框架判断是否匹配」；  
   - 浮盈亏 ≠ 预测成败（保持现有措辞）。

**涉及**：`review_history.py`、复盘 pipeline 入口、`REVIEW_SYSTEM`、`*-review.md` 模板（writer 或独立 render）。

**依赖**：W2-7 digest 回填会明显提高前几周对照质量；可先上对照表（缺字段标 unknown），再回填。

---

### 2.3 缺标的明示（W2-4）

**行为**

- 对每个 `priority=high`（及可选 medium）板块：
  - 用 `sector_map` / 成分 / 深度池 `sector_tag` 判断是否有对应深度池标的。  
- 若无：结论卡 B1 或 A3 旁写：
  - `缺口：{板块} 高优先级，本轮深度池无映射标的 → 仅约束风格/仓位，非漏推个股`  
- 有票但全是 watch：可写「有标的、无进攻动作」区分缺标的。

**涉及**：`sector_map.py`（映射补全）、`decision_stages` 或 writer 聚合、`pipeline` 产出 `sector_coverage[]`。

---

### 2.4 sector_link（W2-5）

**行为**

每条建议 JSON 增加：

```json
"sector_link": {
  "sector": "新能源",
  "sector_priority": "high",
  "sector_prosperity": "up",
  "from_research_rating": "buy",
  "action_rationale_vs_research": "research buy → watch：微观结构禁新买 / 景气未拐点 / …"
}
```

- Prompt：强制填写；无板块承接的 buy/add 视为不合格。  
- Validator：buy/add 缺 `sector_link.sector` → 补推断或降为 watch + override。  
- 结论卡 A3：动作旁显示「← {板块}·{priority}」。

**与第一波关系**：景气 down 禁加仓已存在；sector_link 把「为什么没加/为什么降」写进人可读字段。

---

### 2.5 多 Agent 分歧外显（W2-6）

**行为**

- 消费已有 `multi_agent` / `build_synthesis_audit`（agreed_buys / agent_only / dropped）。  
- 结论卡 A3 或 A 顶增加一行：
  - `主副一致买入：…；仅主/仅副：…；综合否决：…`  
- 无多 Agent 时跳过（不显示空壳）。

**涉及**：`decision_stages.build_synthesis_audit`、`writer.render_conclusion_card`；可选写入 digest。

---

### 2.6 digest 回填（W2-7）

**行为**

- 一次性脚本（如 `scripts/backfill_digests.py`）：
  - 读 `reports/YYYY-MM-DD.json`（及必要时 `.md`）→ 对齐 `reports/digests/*.json` 字段：
    - phase / style / risk / sectors priorities / narratives / microstructure severity 等  
- 不改正文历史报告；只补结构化 digest。  
- 回填后跑一趟复盘对照，抽查 2–3 个旧日。

**涉及**：`decision_digest.py` 字段约定、脚本、文档一行说明。

---

### 2.7 服务器同步（W2-8）

**行为**

- 明确步骤或 `scripts/deploy_server.sh`：
  - rsync 代码（排除 `.venv`、本地 secrets 策略与现网一致）  
  - 依赖如有新增则 `pip install`  
  - `--force` 冒烟；确认报告日期与结论卡新字段出现  
- Cron 仍为 tue/fri；确认 `--skip-optimize` 已无（自优化已删）。

**注意**：先合入本波代码再部署，避免服务器只剩第一波。

---

## 3. 建议实施顺序

```text
1. W2-5 sector_link（prompt + validator + 卡）     ← 分析质量骨架
2. W2-4 缺标的明示（sector_coverage → 卡）
3. W2-1 验证窗口（prompt + 卡 + 轻校验）
4. W2-6 多 Agent 分歧外显（主要 writer）
5. W2-7 digest 回填脚本
6. W2-2 + W2-3 复盘对照表 + 去 5 日噪声
7. W2-8 部署同步 + 冒烟
8. （可选）W2-9 / W2-10
```

依赖关系：对照表（6）受益于回填（5）；部署（7）压轴。

---

## 4. 配置项草案（实施时写入 example）

```yaml
# 第二波；均可默认开启
report:
  show_verify_window: true
  show_sector_gaps: true
  show_agent_divergence: true

advice:
  require_sector_link: true
  require_verify_signals: true   # false=缺失时补默认不降级

review:
  ignore_short_window_days: 5
  horizon_ref_days: 60
  use_dimension_diff_table: true
```

命名以落地时与现有 Config 风格对齐为准。

---

## 5. 验收标准

- [ ] 新跑一轮报告：A3 每条有验证窗口 + sector_link；high 板块无票时有缺标的说明  
- [ ] 有多 Agent 时结论卡可见一致/分歧一行  
- [ ] `*-review.md` 含维度对照表；文案不强调 5 日涨跌成败  
- [ ] 回填后至少 3 个旧 digest 字段变厚；对照表 unknown 减少  
- [ ] 服务器报告模板与本地一致，冒烟成功发信  
- [ ] 单测：sector_link 缺失降级/补全、缺标的检测、diff 表纯函数、verify 默认补全  

---

## 6. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 强制 sector_link/verify 导致 LLM 输出变脆 | 校验层补默认 + override，避免整轮失败 |
| 对照表历史字段空洞 | 先 unknown，再靠回填；不编造历史 |
| 复盘变长 | 对照表限宽（顶层维度 + 持仓动作），LLM 解释限长 |
| 服务器与本地漂移 | W2-8 固定清单；部署后对一下结论卡关键字 |

回滚：配置开关关闭新渲染/强制校验；保留第一波门禁不动。

---

## 7. 一句话

> **第二波不堆新数据源，而是让每条建议能挂板块、能写验证窗口、缺标的说得清，复盘用对照表对答案——外行读得懂，系统也更难「故事自洽、事后无法证伪」。**

下令「做第二波」后按 §3 顺序改代码。
