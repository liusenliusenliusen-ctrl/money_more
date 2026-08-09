# money_more 数据接口目录

精确到**代码实际调用的接口名**。白话说明书见 [`data-sources-guide.md`](data-sources-guide.md)；字段本质/禁区见 [`data-semantics-guide.md`](data-semantics-guide.md)。

依据仓库当前实现梳理。主链路：**AkShare**；可选增强：**Tushare Pro**、**RSS/快讯**。  
若干字段已 **Ak↔Tushare 双源融合**（`source_fuse`）：宏观硬指标、市场/个股两融、质押、股东减持——约定见各表「用途」列与 §6 末。

报告对照：**A1** 宏观环境 · **A2** 展望/事件 · **B1** 板块 · **B2** 个股 · **A3** 动作/硬门禁 · **screen** 选股漏斗。

---

## 1. 行情 / 现货 / 广度

| 接口 | 数据说明 | 用途 |
|------|----------|------|
| `ak.stock_zh_a_spot_em` | 东财全 A 现货（价、涨跌幅、额；含 PE/PB） | 选股宇宙、微观结构、报价 |
| `ak.stock_sh_a_spot_em` | 沪 A 分市场现货 | 全量失败时的拆分备源 |
| `ak.stock_sz_a_spot_em` | 深 A 分市场现货 | 同上 |
| `ak.stock_bj_a_spot_em` | 北交所分市场现货 | 同上 |
| `ak.stock_zh_a_spot` | 新浪全 A 现货（常无可靠 PE/PB） | 现货最终备源 |
| 磁盘 `DiskTTLCache` 键 `spot_em:{as_of}` | 过期/TTL 现货缓存 | 直播失败时的最后兜底 |
| `ak.stock_zh_index_daily(symbol=sh000001)` | 上证指数日线 | A1 阶段/风格 |
| `ak.stock_zh_index_daily(symbol=sz399001)` | 深成指日线 | 同上 |
| `ak.stock_zh_index_daily(symbol=sz399006)` | 创业板指日线 | 同上 |
| `ak.stock_zh_index_daily(symbol=sh000300)` | 沪深300 日线 | A1；个股相对强度 `rs_vs_hs300_20d` |
| `ak.stock_zh_a_hist(symbol, period="daily", adjust="qfq")` | 东财个股日 K（约 180 日） | B2 趋势、因子动量、门禁上下文 |
| `ak.stock_zh_a_daily(symbol=sh/sz+code, adjust="qfq")` | 新浪个股日 K | 日 K 备源 |
| `ak.stock_zt_pool_em(date)` | 涨停池 | 市场广度 / 微观结构 |
| `ak.stock_zt_pool_dtgc_em(date)` | 跌停池 | 同上 |
| `ak.stock_market_activity_legu` | 乐咕市场活跃度 | A1 广度 |

**现货回退链**：`stock_zh_a_spot_em` → 分市场三接口 → `stock_zh_a_spot` → 磁盘缓存。

---

## 2. 板块 / 行业 / 概念

| 接口 | 数据说明 | 用途 |
|------|----------|------|
| `ak.stock_board_industry_summary_ths` | 同花顺行业涨跌/资金摘要 | B1 板块态度、自动赛道 |
| `ak.stock_fund_flow_industry(symbol="即时")` | 同花顺行业资金流 | 板块资金备源② |
| `ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")` | 东财行业资金流排名 | 板块资金备源③；含 `rank_by_change` / `rank_by_inflow` |
| `ak.stock_board_industry_name_ths` | 同花顺行业名称列表 | B1 板块匹配 |
| `ak.stock_board_industry_info_ths(symbol)` | 同花顺行业概况 | B1 |
| `ak.stock_board_industry_index_ths(symbol, start_date, end_date)` | 同花顺行业指数 K | B1 板块趋势 |
| `ak.stock_board_industry_name_em` | 东财行业名称 | 筛股漏斗、成分匹配 |
| `ak.stock_board_industry_cons_em(symbol)` | 东财行业成分股 | screen / B1 |
| `ak.stock_board_concept_name_em` | 东财概念名称 | 主题/概念轮动 |
| `ak.stock_board_concept_cons_em(symbol)` | 东财概念成分股 | 同上 |
| `ak.stock_news_em(symbol=板块名)` | 板块相关新闻 | B1 板块情报 |

**板块资金回退链**：`stock_board_industry_summary_ths` → `stock_fund_flow_industry` → `stock_sector_fund_flow_rank`。

---

## 3. 宏观情报（新闻 / 日历 / 硬指标 / 资金）

| 接口 | 数据说明 | 用途 |
|------|----------|------|
| `pro.cctv_news(date)` | Tushare 联播 | A1 政策主源 |
| `pro.major_news(src="新浪财经")` | Tushare 重大新闻（新浪） | A1 宏观新闻 |
| `pro.major_news(src="华尔街见闻")` | Tushare 重大新闻（见闻） | 同上 |
| `ak.news_cctv` | AkShare 联播 | 政策备源 |
| 快讯/RSS 关键词抽取（`policy_news_source=rss_global_extract`） | 非联播的政策向抽取 | 联播空时降级补位（≠正式联播） |
| `ak.stock_info_global_em` | 东财全球财经快讯 | A1、舆情语料 |
| `ak.stock_info_global_sina` | 新浪全球快讯 | 同上 |
| `ak.news_economic_baidu` | 百度经济日历（前瞻发布日程） | A2 事件观察清单 |
| `ak.macro_cons_gold` | 日历备源之一 | 主日历空时 |
| `ak.news_trade_notify_suspend_baidu` | 日历备源之二 | 同上 |
| `macro_hard_echo`（派生，非外部接口） | 已公布 PMI/CPI/M2 回看 | 仅背景，**不得当作未来日历** |
| `ak.macro_china_pmi` | 中国 PMI（Ak；须按最新期次取，防升序旧月） | A1 硬宏观对照源 |
| `ak.macro_china_cpi` | 中国 CPI | 同上 |
| `ak.macro_china_money_supply` | M2 / 货币供应量 | 同上 |
| `ak.macro_china_shrzgm` | 社会融资规模增量及分项 | A1 宽信用旁路对照（`macro_hard.social_financing`） |
| `ak.macro_china_new_financial_credit` | 新增信贷 | 与社融交叉（目前仍以 Ak 为主） |
| `pro.cn_pmi` → 字段 `PMI010000`（制造业） | Tushare 官方 PMI 序列 | **`macro_hard.pmi` 主源**（`source_fuse.fuse_macro_series`） |
| `pro.cn_cpi` → `nt_yoy` 等 | Tushare CPI | **`macro_hard.cpi` 主源** |
| `pro.cn_m` → `m2_yoy` 等 | Tushare M2 | **`macro_hard.m2` 主源** |
| `pro.sf_month` → `inc_month` 等 | Tushare 社融月增量 | **`macro_hard.social_financing` 主源** |
| `ak.bond_zh_us_rate` | 中美利率（美 2Y/10Y、国开等） | A1 全球流动性 stance |
| `ak.currency_boc_sina(symbol="美元")` | 中行美元兑人民币 | A1 流动性（USD/CNY） |
| `ak.stock_index_pe_lg(symbol="沪深300")` | 沪深300 PE | 股债性价比 ERP → 总仓上限 |
| `ak.macro_china_market_margin_sh` | 沪市两融余额趋势 | A1 杠杆情绪（**市场两融主源**） |
| `ak.macro_china_market_margin_sz` | 深市两融余额趋势 | 同上（补充） |
| `pro.margin(trade_date)` | 分交易所两融；需 SSE+SZSE 齐全才入序列 | Ak 失败时的市场两融备源；与 Ak 比近 5 日方向 |
| `ak.stock_hsgt_fund_flow_summary_em` | 北向资金汇总 | A1 北向（痕迹非聪明钱） |
| `ak.stock_hsgt_hist_em(symbol="北向资金")` | 北向历史净买 | 汇总失败备源；市场概览 |
| `ak.stock_hsgt_hist_em(symbol="沪股通")` | 沪股通历史 | 北向历史再备源 |
| `ak.stock_hot_rank_em` | 东财人气榜 | 拥挤度 |
| `ak.stock_hot_follow_xq` | 雪球关注榜 | 人气榜备源 |
| `ak.index_news_sentiment_scope` | 数库全市场新闻情绪指数 | A1 **旁路温度计**，不进个股打分 |
| 本地 `FinancialSentimentScorer` | 对已采新闻的词典分 | A1 `sentiment_overview`、行业关键词分 |

**政策回退链**：Tushare `cctv_news`/`major_news` → `ak.news_cctv` → 快讯抽取。  
**北向汇总回退链**：`stock_hsgt_fund_flow_summary_em` → `stock_hsgt_hist_em`。  
**人气榜回退链**：`stock_hot_rank_em` → `stock_hot_follow_xq`。

---

## 4. 快讯 / RSS

| 接口 | 数据说明 | 用途 |
|------|----------|------|
| `ak.stock_info_cjzc_em` | 东财财经早餐 | `rss_telegraph` / 重要快讯池 |
| `ak.stock_info_global_ths` | 同花顺快讯 | 快讯备源 |
| `ak.stock_info_global_futu` | 富途快讯 | 快讯备源 |
| `ak.stock_info_global_cls(symbol="全部")` | 财联社电报（全部；易超时） | 电报主源之一 |
| `ak.stock_info_global_cls(symbol="重点")` | 财联社电报（重点） | 同上 |
| `requests.get` + `feedparser`（`config.rss.feeds`） | 自定义 RSS | A1 电报/深度 |
| RSSHub `https://rsshub.app/cls/telegraph` | 财联社电报 RSS | `use_fallback_rss=true` 时 |
| RSSHub `https://rsshub.app/cls/telegraph/red` | 财联社加红电报 | 同上 |
| RSSHub `https://rsshub.app/cls/depth` | 财联社深度 | 同上 |

默认配置偏 **AkShare 直连快讯**；RSSHub 仅在开启 fallback 或自定义 feeds 时启用。

---

## 5. 个股情报（深度池每只）

| 接口 | 数据说明 | 用途 |
|------|----------|------|
| `ak.stock_news_em(symbol=code)` | 个股新闻 | B2、完备性、舆情 |
| `ak.stock_research_report_em(symbol=code)` | 个股研报 | B2 叙事旁证 |
| `ak.stock_comment_em` | 千股千评（全市场表，按轮缓存） | 拥挤 |
| `ak.stock_comment_detail_zhpj_lspf_em(symbol)` | 历史综合评分 | 舆情旁证（不抬分） |
| `ak.stock_comment_detail_scrd_desire_em(symbol)` | 参与意愿 | 拥挤旁证 |
| `ak.stock_hot_follow_xq` | 雪球关注榜（多股复用） | 拥挤 |
| `ak.stock_hot_deal_xq` | 雪球成交榜 | 拥挤 |
| `ak.stock_lhb_detail_em(start_date, end_date)` | 龙虎榜明细 | 完备性（字段 `lhb_records`） |
| `ak.stock_margin_detail_sse(date)` | 上交所个股两融（近若干交易日尝试） | B2；**备源**（Tushare `margin_detail` 优先） |
| `pro.margin_detail(ts_code, start_date, end_date)` | 个股两融明细 | B2 `margin_detail` **主源** |
| `ak.stock_hsgt_hold_stock_em(market="北向"\|"沪股通"\|"深股通", indicator="今日排行"\|"5日排行")` | 北向个股持股排行 | B2；东财常空/「服务器繁忙」 |
| `ak.stock_gpzy_pledge_ratio_em(date)` | 全市场质押比例表（按日缓存） | 硬门禁一侧；与 `pledge_stat` **取 max** |
| `pro.pledge_stat(ts_code)` | 股权质押统计（比例单位 %） | 硬门禁另一侧；`fuse_pledge` |
| `ak.stock_shareholder_change_ths(symbol)` | 股东增减持（同花顺） | 近窗减持 → `force_watch`（并集一侧） |
| `pro.stk_holdertrade(ts_code, …)` | 结构化增减持（`in_de=DE/IN`） | 近窗 DE → `recent_share_reduce`（并集） |
| `ak.stock_financial_analysis_indicator(symbol)` | 东财财务分析指标 | B2、现金流备源 |
| `ak.stock_financial_abstract(symbol)` | 东财财务摘要 | B2、因子质量备源 |
| `ak.stock_individual_fund_flow(stock, market="sh"\|"sz")` | 个股主力净流入 3/5/20 日 | 因子 `fund_flow`（短窗痕迹，非聪明钱） |

---

## 6. Tushare Pro（`pro.*`）

| 接口 | 数据说明 | 用途 |
|------|----------|------|
| `pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")` | 股票基础列表 | **token 探测**（勿用 `trade_cal`，易撞限） |
| `pro.cctv_news(date)` | 联播 | A1 政策 |
| `pro.major_news(src=…)` | 重大新闻 | A1 宏观；板块关键词过滤时用于 B1 |
| `pro.anns_d`（近 30 日） | 公司公告 | 减持门禁、完备性 |
| `pro.anns_d`（近 1 年） | 公告扩展 | 完备性 / 交叉 |
| `pro.fina_indicator(ts_code, limit=4)` | 财务指标（含负债率等） | 因子质量、负债门禁（≥75/90，金融豁免） |
| `pro.income` / `pro.balancesheet` / `pro.cashflow` | 三大报表 | B2、OCF 质量 |
| `pro.daily_basic`（约 3 年窗口） | 日频估值 + 历史分位 | 估值因子、双源核对 |
| `pro.news(src="sina", …)` | 新浪新闻归档（按代码过滤） | B2 舆情池 |
| `pro.stock_company(ts_code)` | 公司基本信息 | 行业/公司上下文 |
| `pro.forecast` | 业绩预告 | 暴雷硬门禁、盈利修正 |
| `pro.share_float` | 解禁日程 | ≤30 日 `force_watch` |
| `pro.pledge_stat` | 质押统计 | 见 §5；门禁保守合并 |
| `pro.stk_holdertrade` | 股东增减持 | 见 §5 |
| `pro.cn_pmi` / `pro.cn_cpi` / `pro.cn_m` / `pro.sf_month` | 宏观硬序列 | 见 §3；写入 `macro_hard` + `macro_hard_meta` |
| `pro.margin` / `pro.margin_detail` | 市场/个股两融 | 见 §3 / §5 |

可用性取决于账号积分/权限。常见限制：部分接口「无权限」；部分「1 次/分钟」。探测失败勿再写成「未配置 token」。  
双源合并约定见 `money_more.data.source_fuse`（门禁取严；序列主源优先；冲突标 `agreement=conflict`，**不平均**）。

---

## 7. 派生 / 本地（无外部 API，但进报告）

| 名称 | 说明 | 用途 |
|------|------|------|
| `sentiment_overview` | 宏观新闻池词典分 | A1 温度 |
| `industry_sentiment_index` | 宏观新闻撞板块名的关键词分 | 弱行业线索（非调研） |
| `macro_event_signals` | 日历 + 新闻事件清单 | A2 观察 |
| `crowding_signal` | 人气/雪球/意愿聚合 | 因子拥挤惩罚 |
| `factor_scorecard` | 估值/动量/资金/舆情/质量/叙事 | B2① 加权分；报告分列拥挤与新闻语调 |
| `hard_gates` / `cross_check` | ST、涨跌停、解禁、负债、质押、减持、双源价 | A3 动作约束 |
| `equity_bond`（ERP） | 股债性价比 → 总仓上限 | 组合纪律 |
| `info_completeness` | 大波动是否有公告/新闻解释 | 缺口 → 观望 |
| `build_data_sources_ledger` | 本轮各源成功/降级摘要 | `*-datasources.md` |
| `macro_hard_meta` | 硬指标双源：`primary` / `agreement` / 期次差 | A1 / 台账；冲突不平均 |
| `source_fuse.*` | 质押 max、减持并集、宏观/两融融合 | `intelligence` 接线后供门禁与上下文 |

---

## 配置开关速查

| 开关 | 影响 |
|------|------|
| `intelligence.enabled` | 宏观/板块/个股情报总闸 |
| `screen.enabled` | 全市场筛股与现货宇宙 |
| `tushare.enabled` + `TUSHARE_TOKEN` | 公告/财务/估值/预告/解禁；质押·增减持·宏观硬指标·两融双源 |
| `rss.*` / `rss.use_fallback_rss` | 快讯与 RSSHub 备源 |
| `sentiment.enabled` | 词典舆情与拥挤相关打分 |

细节见 `config.yaml.example`。自检（不调 LLM）：`python -m money_more doctor`。
