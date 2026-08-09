# money_more 优化方案 · 第四波

> 状态：**代码已落地**（2026-08-08）  
> 前置：第一～三波代码已落地（见 v2 / wave2 / wave3）  
> 本波主题：**数据源深治**（把第三波临时策略变成可诊断、可配置、可降级的通路）  
>  
> **已拍板（按建议默认）**：轨 A+B 做；不做 Tushare 升积分 / Cursor / 新增强源；无自建 RSSHub → fallback 仍关；**不冒烟**（全部完成后统一测）

---

## 0. 前三波回顾 → 第四波该干什么

| 波次 | 主题 | 结果 |
|------|------|------|
| 一 | 更钝、管住手 | 暴涨剔除、框架闸、社融、微观分档 |
| 二 | 能对答案 | 验证窗口、sector_link、复盘对照、digest |
| 三 | 临时稳住 + 进池 P1 + 薄补 | `force_direct`、多日成交额、观察/升权扩、DQ/拐点 |
| 四 | 数据源深治 + 工程薄补 | doctor 双轨探测、bypass 模式、错误分级、快讯配置、Token/辩论 |

---

## 1. 范围清单

### 轨 A — 数据源深治（已做）

| ID | 项 | 验收 |
|----|-----|------|
| W4-1 | 东财连通探测 | `doctor`：env vs force_direct 各打 spot |
| W4-2 | 会话级绕过代理 | `data.eastmoney_bypass`: env_clear / session_trust_env_false / both |
| W4-3 | EM 失败分级 | errors 带 `[proxy|timeout|empty|http|other]` |
| W4-4 | doctor 一页诊断 | spot/hist/热榜/板块资金/快讯表 + 建议三行 |
| W4-5 | 快讯链配置化 | `rsshub_base`、超时、fallback 默认关；台账/卡提示拓扑 |
| W4-6 | 降级话术统一 | `degrade_messages` + writer/ledger |

### 轨 B — 工程薄补（已做）

| ID | 项 | 验收 |
|----|-----|------|
| W4-7 | Token 收紧 | 观察扩板块 compact payload + 短 summary 提示 |
| W4-8 | 辩论去盘面化 | DEBATE_SYSTEM 偏盈利/估值/失效/信息完备 |
| W4-9 | 文档 + TODO | 本文件 + TODO 同步 |

### 明确不做

- Tushare 升积分、Cursor secondary、新 A2 增强源  
- 统一冒烟 / 部署（仍留全部完成后）

---

## 2. 关键配置

```yaml
data:
  eastmoney_force_direct: true
  eastmoney_bypass: both    # env_clear | session_trust_env_false | both | off

rss:
  use_fallback_rss: false
  cls_timeout_sec: 8
  rsshub_base: ""           # 自建后填写，并视需要打开 use_fallback_rss
```

自检：

```bash
.venv/bin/python -m money_more doctor
```

---

## 3. 一句话

> **第四波把东财/快讯从「临时能跑」做成「可探测、可配置、失败说得清」，并顺带收紧观察扩 Token、辩论去盘面；升积分/Cursor/新源仍 TODO。**
