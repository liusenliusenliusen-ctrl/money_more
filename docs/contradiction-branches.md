# 矛盾处理逻辑：硬事实、叙事与「矛盾分支」

> 面向：读报告时看到「矛盾分支（若…则…）」想弄懂系统在干什么的人。  
> 代码：`analysis/framework_gates.py`（检测与建分支）→ `decision_validator.py`（落闸）→ `report/writer.py`（结论卡展示）。  
> 配置：`config.yaml` → `framework_gates.*`

---

## 1. 要解决什么问题

中长线研究里经常同时出现两套声音：

| 类型 | 例子 | 特点 |
|------|------|------|
| **硬事实** | PMI&lt;50、融资余额近窗收缩 | 可核对数字；滞后但难辩驳 |
| **叙事 / 软信号** | 「政策托底」「成长主线」「国产替代」 | 来自新闻、板块涨跌、LLM 概括；可能对，也可能超前或空转 |

人（和 LLM）容易做的错事是**假和解**：

> PMI 弱 + 科技涨 + 央行放水 → 「综合来看还行，可以偏多一点」

矛盾在文字里消失了，仓位却已经偏向叙事。  
MM 的纪律是：**冲突时不平均抹掉；硬事实优先管闸；每条冲突拆成可跟踪的「若改善 / 若恶化」分支。**

---

## 2. 报告里那句口号的白话版

原句（偏压缩）：

> 硬事实与叙事冲突不平均抹掉；按分支跟踪，确认一条动一条。

**白话：**

硬数据和故事打架时，先听硬数据；把冲突拆开跟，哪一条被后续数据确认，就只调整那一条对应的动作——不要各打五十大板，假装已经没有矛盾。

---

## 3. 「分支」是什么（不是「相对主线的旁支」）

这里的「分支」**不是**「主线题材 vs 支线题材」，而是：

> 对**同一条已激活的矛盾**，拆出的两条后续路径（if / else）。

| 概念 | 含义 |
|------|------|
| **主线 / 主情景** | 当前总判断：环境、主驱动、配置倾向（A1/A2） |
| **矛盾分支** | 硬事实与叙事冲突时的 **「若改善 / 若恶化」跟踪路径** |
| **争议 / 未验证假说** | 尚未证实的侧栏叙事；须确认才升权（另一套东西） |

关系可以记成：

- **主线** = 剧本总纲  
- **矛盾分支** = 剧本里某个关键开关的两种走法  
- **争议侧栏** = 还没写进主剧本的假说

主线可以照常写「偏成长 / 震荡」；若 PMI 已收缩，就**额外**挂上景气分支，避免用主线故事把 PMI 抹平。

---

## 4. 端到端流程

```
宏观情报（macro_hard / 两融等）
        │
        ▼
detect_hard_contradictions   ← 规则：PMI<50、融资近窗收缩…
        │
        ├─ + 市场 LLM 的 contradictions 散文
        ▼
contradiction_active ?
        │
        ├─ 是 → 建 contradiction_branches（展示）
        │       + haircut / 禁进攻 / 拦 phase 升档（动作）
        │
        ▼
decision_validator 落闸到 buy/add
        │
        ▼
结论卡「矛盾分支（若…则…）」
```

要点：**分支文案是给人跟踪用的操作说明书；真正拦仓位的是同套框架闸的布尔/系数。**

---

## 5. 硬矛盾怎么被检测出来

`detect_hard_contradictions(macro_intel)`（规则层，非 LLM）：

| 条件 | 写入的 flag 示例 |
|------|------------------|
| 制造业 PMI（或同类字段）**&lt; 50** | `PMI收缩(49.2)` |
| 融资余额近 5 日变化 **&lt; 0** | `融资余额近窗收缩(-1.2%)` |

社融等可作旁路输入，**当前不单独列成硬矛盾 flag**（避免字段口径不稳时误触发）。

同时，市场分析 JSON 里的 `contradictions` / `key_contradictions`（LLM 散文）也会参与 `contradiction_active`；但**建分支时优先用硬 flag**；没有硬 flag、仅有散文时，才退化成「叙事矛盾」通用分支。

---

## 6. 矛盾激活后系统做什么（动作层）

当 `contradiction_active == true`（默认配置下）：

| 闸 | 默认行为 | 配置键 |
|----|----------|--------|
| 总仓 / 进攻折扣 | 有效总仓 × `contradiction_haircut`（默认 0.8） | `framework_gates.contradiction_haircut` |
| 禁进攻 buy/add | 新开/加仓被压成 watch/hold | `framework_gates.contradiction_block_offensive` |
| phase / 风格过快升乐观 | 可拦升档、risk 维持偏谨慎 | `framework_gates.phase_upgrade_needs_confirm` |

报告里常见的：

> 硬事实/叙事矛盾激活 → 禁止进攻 buy

就是这一层，不是 LLM 临时起意。

### 与「板块景气 down」的关系（相邻、不同）

| 机制 | 触发 | 作用对象 |
|------|------|----------|
| **矛盾激活** | 宏观硬 flag / LLM 矛盾列表 | 组合层：禁进攻、haircut、升档 |
| **景气 down** | 板块/个股 `prosperity=down` | 个股层：该票禁止 buy/add（拐点+证据可豁免） |

PMI 收缩既可能激活宏观矛盾分支，也可能通过板块景气映射间接影响个股；读报告时不要合成一条。

---

## 7. 「矛盾分支」文案怎么生成

`build_contradiction_branches(...)` 按硬 flag 模板化（第五波 A4）：

### 景气（PMI）

- **事实**：如 `PMI收缩(49.2)`  
- **若改善**：PMI 回 50 上方且新订单改善 → 解除「禁进攻」，phase 可升档  
- **若恶化**：PMI 连续两月 &lt;50 → 维持防御，成长升档继续被拦截  

### 杠杆资金（融资）

- **若改善**：融资余额止跌回升 5 日 → 风险权重下调，可评估进攻仓位  
- **若恶化**：融资余额继续收缩 → 总仓 haircut 维持，禁止追题材  

### 其它硬 flag

- 通用：指标回中性区 → 解除对应闸；继续恶化 → 维持/加码防御  

### 仅有 LLM 散文矛盾时

- topic=`叙事矛盾`；若改善=叙事被硬数据证实则升权；若恶化=被证伪则降权  

展示位置：结论卡 A1 内「矛盾分支（若…则…）」，与「争议与未验证假说」分区并列——前者跟**已触发的硬冲突**，后者跟**未证实假说**。

---

## 8. 「确认一条动一条」怎么执行（读法 + 代码）

以 PMI 分支为例：

1. **现在**：49.2 已确认偏弱 → 进攻闸按现状关掉（合理）。  
2. **不要做的事**：因为半导体涨、政策有逆回购，就在脑子里把 PMI「综合」没了。  
3. **下周/下月**：只核对这条分支的改善/恶化条件。  
   - 改善条件满足 → 才讨论解除禁进攻、是否允许 phase 升档。  
   - 恶化条件满足 → 继续防御，不因叙事热闹而升档。  
4. **另一条矛盾**（若有融资分支）单独看；PMI 修好了不等于融资分支自动解除。

这就是「确认一条动一条」：**按开关逐个松/紧，不打包平均。**

### 跨轮机读（已落地）

- 每轮 `decision_digest` 写入带稳定 `branch_id` 的 `contradiction_branches`。  
- 下轮 `evaluate_prior_contradiction_branches`：用 PMI / 融资余额近 5 日变化标 `improved` / `worsened` / `unchanged`。  
- **硬分支（`pmi_contraction` / `margin_shrink`）未 `improved` 时继续 `contradiction_active`**，即使本轮 LLM 散文矛盾已空、或缺宏观字段（`unchanged`）也不得靠叙事单独松闸。  
- 报告可展示 `prior_status`；`unresolved_prior_branches` 可审计。

---

## 9. 和主驱动、认错条件怎么一起读

| 报告块 | 角色 |
|--------|------|
| **主驱动** `primary_driver` | LLM 对中期定价第一因素的综合判断（可含硬+软） |
| **矛盾分支** | 规则层：已触发冲突的跟踪清单 + 闸门说明书 |
| **若出现则认错** `invalidation` | 主情景被推翻的条件（常与分支「恶化」侧呼应） |
| **争议假说** | 未进主线的情景；确认前不得单独买 |

健康读法：

1. 先看主线环境与主驱动；  
2. 再看矛盾分支：现在卡在哪条硬事实上；  
3. 建议段若写「禁进攻」，对照分支与 validation_overrides；  
4. 争议侧栏单独当观察清单，不与分支混读。

---

## 10. 设计原则小结

1. **硬事实优先于叙事**——冲突时先收紧，再等确认。  
2. **不平均抹掉**——禁止「一半好一半坏 → 折中可买」。  
3. **分支 = if/else，不是旁支题材**。  
4. **展示与落闸同源**——卡上的「若…则…」对应框架闸语义，不是装饰文案。  
5. **升乐观要慢、降风险可以快**——与中长线去短线灵敏度一致。

---

## 11. 相关代码与配置

| 路径 | 作用 |
|------|------|
| `src/money_more/analysis/framework_gates.py` | `detect_hard_contradictions` / `build_contradiction_branches` / `build_framework_gate_state` |
| `src/money_more/analysis/decision_validator.py` | `contradiction_active` → haircut、禁进攻；景气 down 个股闸 |
| `src/money_more/report/writer.py` | 结论卡渲染矛盾分支 |
| `framework_gates.contradiction_haircut` | 默认 `0.8` |
| `framework_gates.contradiction_block_offensive` | 默认 `true` |
| `framework_gates.prosperity_block_adds` | 默认 `true`（个股景气闸） |
| `framework_gates.phase_upgrade_needs_confirm` | 默认 `true` |

报告解读入口另见 [`how-to-read-report.md`](how-to-read-report.md)。
