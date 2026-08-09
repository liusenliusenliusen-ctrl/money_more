# 待办优化清单

记录讨论中认可、但**暂不实施**或**排期靠后**的优化项。  
按性价比排序；动手前先勾选范围，避免一次铺太开。

最后更新：2026-08-08  

**第一波（已落地）**：[`optimization-plan-v2.md`](optimization-plan-v2.md)  
**第二波（已落地）**：[`optimization-plan-wave2.md`](optimization-plan-wave2.md)  
**第三波（已落地）**：[`optimization-plan-wave3.md`](optimization-plan-wave3.md)  
**第四波（已落地）**：[`optimization-plan-wave4.md`](optimization-plan-wave4.md)（数据源深治 + Token/辩论薄补；**未**升积分/Cursor/新源；**未**冒烟）  
**第五波（方案）**：[`optimization-plan-wave5.md`](optimization-plan-wave5.md)（A0 止血 + 工程修 + 证据链/归因）  
**第五波阶段一（已落地）**：A0-1~5 + C5/C6/C7/C9/C11 + 测试  
**第五波阶段二（已落地）**：A3 证据出处 / A4 矛盾分支 / B1 验证命中率台账 / C1 截断率指标 / C2 影子回放脚本 + 测试（177/177 过）  
**未做**：统一部署 + 冒烟；Tushare 升积分；Cursor 9b；新增强源；轨 A1/A2（盈利修正评分、拥挤 vs 景气判据）与 B2–B4（stage 归因/集中度/成本）按需另开  
本文件保留未排期项；与上述方案冲突时以方案文档为准。

---

## 仍暂缓 / 按需

### Tushare 积分升级
- 付费开关；升级后现有路径即可吃满 fina/forecast/anns/联播/重大新闻。  
- 代码侧：`doctor` 已轻量 probe；不刷受限接口。

### Cursor 副分析师压缩（9b）
- 继续 DeepSeek 双角色。  
- 恢复前：决策 payload 压缩（约 &lt;60KB）+ 超时杀 bridge。

### 可选增强源（§13）
- 居民存款 / 一致预期 / FRED / 增减持解禁等：主通路已深治，**仍按需再开**，避免堆料。  
- 若自建 RSSHub：填 `rss.rsshub_base` 并视情况 `use_fallback_rss: true`。

### Token / 成本（残余）
- 第四波已砍观察扩板块输入；若仍贵：低优先级板块进一步缩写、维持仅决策多 Agent。

---

## 明确不优先

- 短期舆情/题材堆料、点位预测、报告复杂 UI  
- 动 ERP / 止损 / 硬门禁宪法  

---

## 建议的下手顺序（更新）

0–4. **第一～四波（已完成代码）**  
5. **统一部署 + 冒烟**（你宣布全部完成后）  
6. Tushare 升积分 / Cursor 9b / 增强源（按需）  

勾选：

- [x] 第一波 v2  
- [x] 第二波 W2  
- [x] 第三波 W3  
- [x] 第四波 W4（轨 A+B）  
- [ ] 统一部署 + 冒烟  
- [ ] Tushare 积分升级  
- [ ] Cursor secondary 压缩  
- [ ] 其余增强源  
