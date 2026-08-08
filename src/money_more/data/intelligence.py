from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import akshare as ak
import pandas as pd

from money_more.analysis.sentiment import (
    FinancialSentimentScorer,
    assess_sector_crowding,
    assess_stock_crowding,
    build_industry_sentiment_index,
    build_macro_event_signals,
    build_market_news_sentiment_scope,
)
from money_more.config import AppConfig
from money_more.data.as_of import (
    filter_calendar_upcoming,
    filter_records_by_date,
    parse_as_of,
    parse_macro_period_date,
    parse_record_date,
    recent_weekdays,
    ymd,
)
from money_more.data.global_liquidity import fetch_global_liquidity
from money_more.data.fetcher import (
    _df_row_to_dict,
    _match_board_name,
    _safe_float,
    build_sector_money_flow,
    build_xueqiu_hot_snapshot,
    fetch_hot_rank_with_fallback,
    fetch_sector_board_summary,
    normalize_code,
)
from money_more.data.rss_feeds import RssFeedFetcher
from money_more.data.tushare_source import TushareSource


def _records(df: pd.DataFrame | None, limit: int = 10) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return df.head(limit).to_dict(orient="records")


def _macro_records_from_df(df: pd.DataFrame | None, limit: int = 6) -> list[dict[str, Any]]:
    """AkShare PMI/CPI/M2 等序列按时间降序，取最新 limit 条。"""
    if df is None or df.empty:
        return []
    return df.head(limit).to_dict(orient="records")


def _macro_records_newest(df: pd.DataFrame | None, limit: int = 6) -> list[dict[str, Any]]:
    """兼容升序/降序宏观表：用月份字段选最新，再取最近 limit 条（新→旧）。"""
    if df is None or df.empty:
        return []
    work = df.copy()
    month_col = None
    for c in work.columns:
        if "月" in str(c) or str(c).lower() in ("month", "date", "日期"):
            month_col = c
            break
    if month_col is None:
        return work.tail(limit).iloc[::-1].to_dict(orient="records")

    def _row_period(val: Any) -> date | None:
        parsed = parse_macro_period_date({month_col: val}) or parse_record_date({month_col: val})
        if parsed:
            return parsed
        text = str(val or "").strip()
        if len(text) == 6 and text.isdigit():
            try:
                return date(int(text[:4]), int(text[4:6]), 1)
            except ValueError:
                return None
        return None

    try:
        work["_sort_dt"] = work[month_col].map(_row_period)
        work = work.dropna(subset=["_sort_dt"]).sort_values("_sort_dt", ascending=False)
        return work.drop(columns=["_sort_dt"]).head(limit).to_dict(orient="records")
    except Exception:
        return work.tail(limit).iloc[::-1].to_dict(orient="records")


def _filter_df(df: pd.DataFrame, code: str, code_cols: tuple[str, ...] = ("代码", "股票代码")) -> pd.DataFrame:
    code = normalize_code(code)
    for col in code_cols:
        if col in df.columns:
            series = df[col].astype(str).str.replace(r"\D", "", regex=True).str[-6:].str.zfill(6)
            matched = df[series == code]
            if not matched.empty:
                return matched
    return df.iloc[0:0]


class IntelligenceFetcher:
    """新闻、政策、研报、舆情、Tushare、RSS 等多源情报采集与舆情打分。"""

    def __init__(self, config: AppConfig, as_of: date | str | None = None) -> None:
        self.config = config
        self.as_of = parse_as_of(as_of)
        self.max_items = config.intelligence.max_news_per_source
        self.news_lookback_days = int(getattr(config.intelligence, "news_lookback_days", 14))
        self.scorer = FinancialSentimentScorer()
        self.tushare = (
            TushareSource(config.tushare_token, as_of=self.as_of) if config.tushare.enabled else None
        )
        if self.tushare:
            self.tushare.probe()
        self.rss = RssFeedFetcher(
            feeds=config.rss.feeds or None,
            max_items_per_feed=config.rss.max_items_per_feed,
            timeout=int(getattr(config.rss, "cls_timeout_sec", 8) or 8),
            cls_direct=config.rss.cls_direct,
            use_fallback_rss=config.rss.use_fallback_rss,
            rsshub_base=str(getattr(config.rss, "rsshub_base", "") or ""),
        ) if config.rss.enabled else None
        self._rss_cache: dict[str, Any] | None = None
        self._tushare_macro_cache: dict[str, Any] | None = None
        self._comment_df: pd.DataFrame | None = None
        self._hot_rank_df: pd.DataFrame | None = None
        self._hot_rank_error: str | None = None
        self._hot_rank_source: str | None = None
        self._hot_rank_warnings: list[str] = []
        self._xueqiu_follow_df: pd.DataFrame | None = None
        self._xueqiu_deal_df: pd.DataFrame | None = None
        self._sector_summary_cache: tuple[pd.DataFrame, str, list[str]] | None = None
        self._pledge_ratio_df: pd.DataFrame | None = None
        self._pledge_ratio_error: str | None = None
        self._northbound_hold_df: pd.DataFrame | None = None
        self._northbound_hold_error: str | None = None

    def set_as_of(self, as_of: date | str | None) -> None:
        self.as_of = parse_as_of(as_of)
        if self.tushare:
            self.tushare.set_as_of(as_of)

    def reset_run_cache(self) -> None:
        self._rss_cache = None
        self._tushare_macro_cache = None
        self._comment_df = None
        self._hot_rank_df = None
        self._hot_rank_error = None
        self._hot_rank_source = None
        self._hot_rank_warnings = []
        self._xueqiu_follow_df = None
        self._xueqiu_deal_df = None
        self._sector_summary_cache = None
        self._pledge_ratio_df = None
        self._pledge_ratio_error = None
        self._northbound_hold_df = None
        self._northbound_hold_error = None

    def _get_comment_df(self) -> pd.DataFrame:
        if self._comment_df is not None:
            return self._comment_df
        try:
            self._comment_df = ak.stock_comment_em()
        except Exception:
            self._comment_df = pd.DataFrame()
        return self._comment_df

    def _get_hot_rank_df(self) -> pd.DataFrame:
        if self._hot_rank_df is not None:
            return self._hot_rank_df
        self._hot_rank_error = None
        self._hot_rank_source = None
        self._hot_rank_warnings = []
        df, source, warnings = fetch_hot_rank_with_fallback(limit=100)
        self._hot_rank_source = source or None
        self._hot_rank_warnings = warnings
        if not df.empty:
            self._hot_rank_df = df
        else:
            self._hot_rank_df = pd.DataFrame()
            self._hot_rank_error = "; ".join(warnings) if warnings else "hot_rank_empty"
        return self._hot_rank_df

    def _get_xueqiu_dfs(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """雪球关注/成交榜：单次拉取、多股复用（避免每只股票重复请求）。"""
        if self._xueqiu_follow_df is not None:
            return self._xueqiu_follow_df, self._xueqiu_deal_df or pd.DataFrame()
        follow = pd.DataFrame()
        deal = pd.DataFrame()
        try:
            raw = ak.stock_hot_follow_xq()
            if raw is not None and not raw.empty:
                follow = raw
        except Exception:
            pass
        try:
            raw = ak.stock_hot_deal_xq()
            if raw is not None and not raw.empty:
                deal = raw
        except Exception:
            pass
        self._xueqiu_follow_df = follow
        self._xueqiu_deal_df = deal
        return follow, deal

    def _get_sector_summary(self) -> tuple[pd.DataFrame, str, list[str]]:
        if self._sector_summary_cache is not None:
            return self._sector_summary_cache
        self._sector_summary_cache = fetch_sector_board_summary()
        return self._sector_summary_cache

    def _get_pledge_ratio_df(self) -> pd.DataFrame:
        """全市场质押比例表（按日），单次拉取多股复用。"""
        if self._pledge_ratio_df is not None:
            return self._pledge_ratio_df
        self._pledge_ratio_error = None
        last_err = ""
        for date_str in recent_weekdays(self.as_of, 5):
            try:
                df = ak.stock_gpzy_pledge_ratio_em(date=date_str)
                if df is not None and not df.empty and "股票代码" in df.columns:
                    self._pledge_ratio_df = df
                    return self._pledge_ratio_df
                last_err = f"pledge_ratio_empty:{date_str}"
            except Exception as exc:
                last_err = str(exc)
                continue
        self._pledge_ratio_df = pd.DataFrame()
        self._pledge_ratio_error = last_err or "pledge_ratio_unavailable"
        return self._pledge_ratio_df

    def _get_northbound_hold_df(self) -> pd.DataFrame:
        """北向个股持股排行（东财）；单次拉取多股复用。

        AkShare 合法 market 为 {北向, 沪股通, 深股通}，旧代码误传「北向持股」会导致
        filter 为空且易触发 result=None → TypeError。
        """
        if self._northbound_hold_df is not None:
            return self._northbound_hold_df
        self._northbound_hold_error = None
        last_err = ""
        # 今日排行在周末/节假日常空，回退 5 日
        for market, indicator in (
            ("北向", "今日排行"),
            ("北向", "5日排行"),
            ("沪股通", "今日排行"),
            ("深股通", "今日排行"),
        ):
            try:
                df = ak.stock_hsgt_hold_stock_em(market=market, indicator=indicator)
                if df is not None and not df.empty:
                    self._northbound_hold_df = df
                    return self._northbound_hold_df
                last_err = f"northbound_hold_empty:{market}/{indicator}"
            except TypeError as exc:
                # AkShare 在 data_json['result'] is None 时裸下标
                last_err = (
                    f"northbound_hold_api_null_result:{market}/{indicator}: {exc} "
                    "（东财常返回「服务器繁忙」或页面日期陈旧）"
                )
            except Exception as exc:
                last_err = f"northbound_hold:{market}/{indicator}: {exc}"
        self._northbound_hold_df = pd.DataFrame()
        self._northbound_hold_error = last_err or "northbound_hold_unavailable"
        return self._northbound_hold_df

    def _get_rss_bundle(self) -> dict[str, Any]:
        if self._rss_cache is not None:
            return self._rss_cache
        if not self.rss:
            self._rss_cache = {"combined": [], "errors": []}
            return self._rss_cache
        self._rss_cache = self.rss.fetch_all()
        self._apply_sentiment(self._rss_cache, "combined")
        return self._rss_cache

    def _get_tushare_macro(self) -> dict[str, Any]:
        if self._tushare_macro_cache is not None:
            return self._tushare_macro_cache
        if not self.tushare or not self.tushare.available:
            reason = "Tushare 未启用或无 token"
            if self.tushare and getattr(self.tushare, "_probe_error", None):
                reason = f"Tushare 不可用: {self.tushare._probe_error}"
            self._tushare_macro_cache = {"items": [], "errors": [reason]}
            return self._tushare_macro_cache
        try:
            self._tushare_macro_cache = self.tushare.fetch_macro_news(limit=self.max_items)
        except RuntimeError as exc:
            self._tushare_macro_cache = {"items": [], "errors": [str(exc)]}
        self._apply_sentiment(self._tushare_macro_cache, "items")
        return self._tushare_macro_cache

    def _apply_sentiment(self, payload: dict[str, Any], news_key: str = "news") -> None:
        if not self.config.sentiment.enabled:
            return
        items = payload.get(news_key) or []
        if items:
            payload["sentiment_analysis"] = self.scorer.score_news_items(items)

    def fetch_macro_intelligence(self) -> dict[str, Any]:
        """宏观层：政策新闻、全球财经、RSS 快讯、Tushare 宏观、资金、舆情打分。"""
        result: dict[str, Any] = {
            "as_of": self.as_of.isoformat(),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "policy_news": [],
            "global_news": [],
            "economic_calendar": [],
            "margin_trend": {},
            "northbound_summary": [],
            "market_hot_rank": [],
            "sector_money_flow": {},
            "rss_telegraph": [],
            "rss_important": [],
            "tushare_macro_news": [],
            "sentiment_overview": {},
            "macro_hard": {},
            "global_liquidity": {},
            "errors": [],
        }

        policy_ak_raw: list[dict[str, Any]] = []
        policy_ak: list[dict[str, Any]] = []
        try:
            cctv = ak.news_cctv()
            policy_ak_raw = _records(cctv, self.max_items)
            policy_ak = filter_records_by_date(policy_ak_raw, self.as_of, lookback_days=self.news_lookback_days)
        except Exception as exc:
            result["errors"].append(f"新闻联播: {exc}")

        tushare_macro = self._get_tushare_macro()
        tushare_items = filter_records_by_date(
            tushare_macro.get("items") or [], self.as_of, lookback_days=self.news_lookback_days
        )
        if tushare_items:
            result["policy_news"] = tushare_items[: self.max_items]
        elif policy_ak:
            result["policy_news"] = policy_ak
        elif policy_ak_raw:
            # 无新鲜政策时保留最近条目并标记陈旧，避免 LLM 完全无政策上下文
            result["policy_news"] = policy_ak_raw[: min(3, self.max_items)]
            result["policy_news_stale"] = True
            result["errors"].append("policy_news_stale_fallback")
        if not result["policy_news"]:
            result["errors"].append("policy_news_stale_or_empty")
        result["errors"].extend(tushare_macro.get("errors") or [])
        result["tushare_macro_news"] = tushare_items[: self.max_items] if tushare_items else (
            tushare_macro.get("items") or []
        )[: self.max_items]
        try:
            global_em = ak.stock_info_global_em()
            result["global_news"] = filter_records_by_date(
                _records(global_em, self.max_items), self.as_of, lookback_days=self.news_lookback_days
            )
        except Exception as exc:
            result["errors"].append(f"全球财经(东财): {exc}")

        try:
            global_sina = ak.stock_info_global_sina()
            sina_records = filter_records_by_date(
                _records(global_sina, min(5, self.max_items)), self.as_of, lookback_days=self.news_lookback_days
            )
            result["global_news_sina"] = sina_records
            sina_payload = {"news": sina_records}
            self._apply_sentiment(sina_payload, "news")
            if sina_payload.get("sentiment_analysis"):
                result["global_news_sina_sentiment"] = sina_payload["sentiment_analysis"]
        except Exception as exc:
            result["errors"].append(f"全球财经(新浪): {exc}")

        try:
            cal = ak.news_economic_baidu()
            result["economic_calendar"] = filter_calendar_upcoming(
                _records(cal.head(self.max_items * 2), self.max_items * 2),
                self.as_of,
            )[: self.max_items]
        except Exception as exc:
            result["errors"].append(f"经济日历: {exc}")

        if not result["economic_calendar"]:
            # 备源：财经日历（若可用）
            for fn_name in ("macro_cons_gold", "news_trade_notify_suspend_baidu"):
                fn = getattr(ak, fn_name, None)
                if fn is None:
                    continue
                try:
                    df = fn()
                    if df is not None and not df.empty:
                        result["economic_calendar_alt"] = _records(df.tail(self.max_items), self.max_items)
                        result["errors"].append("economic_calendar_primary_empty_used_alt")
                        break
                except Exception:
                    continue

        # 宏观硬数据：PMI / CPI / M2 / 社融（失败则跳过）
        macro_hard: dict[str, Any] = {}
        for label, fn in [
            ("pmi", getattr(ak, "macro_china_pmi", None)),
            ("cpi", getattr(ak, "macro_china_cpi", None)),
            ("m2", getattr(ak, "macro_china_money_supply", None)),
        ]:
            if fn is None:
                continue
            try:
                df = fn()
                if df is not None and not df.empty:
                    macro_hard[label] = _macro_records_from_df(df, 6)
            except Exception as exc:
                result["errors"].append(f"宏观{label}: {exc}")
        # 社融：国内宽信用旁路（注意部分接口为时间升序）
        try:
            shr_fn = getattr(ak, "macro_china_shrzgm", None)
            if shr_fn is not None:
                shr_df = shr_fn()
                shr_recs = _macro_records_newest(shr_df, 6)
                if shr_recs:
                    macro_hard["social_financing"] = shr_recs
                    macro_hard["shrzgm"] = shr_recs  # 兼容别名
        except Exception as exc:
            result["errors"].append(f"宏观社融: {exc}")
        try:
            credit_fn = getattr(ak, "macro_china_new_financial_credit", None)
            if credit_fn is not None:
                credit_df = credit_fn()
                credit_recs = _macro_records_from_df(credit_df, 6)
                if credit_recs:
                    macro_hard["new_credit"] = credit_recs
        except Exception as exc:
            result["errors"].append(f"宏观信贷: {exc}")
        result["macro_hard"] = macro_hard

        # 全球流动性硬指标（美债 + USD/CNY）——主线宏观外因
        try:
            result["global_liquidity"] = fetch_global_liquidity(self.as_of)
            result["errors"].extend(result["global_liquidity"].get("errors") or [])
        except Exception as exc:
            result["errors"].append(f"global_liquidity: {exc}")
            result["global_liquidity"] = {"stance": "unknown", "errors": [str(exc)]}

        # 语义：已公布硬指标 ≠ 未来经济日历。禁止写入 economic_calendar。
        if not result["economic_calendar"] and macro_hard:
            echo = _macro_hard_echo_from_macro(macro_hard, self.as_of)
            if echo:
                result["macro_hard_echo"] = echo[: self.max_items]
                result["errors"].append("economic_calendar_empty_macro_hard_echo_only")

        try:
            margin = ak.macro_china_market_margin_sh()
            if not margin.empty:
                tail = margin.tail(10)
                latest = tail.iloc[-1]
                prev = tail.iloc[-5] if len(tail) >= 5 else tail.iloc[0]
                result["margin_trend"] = {
                    "latest": _df_row_to_dict(latest),
                    "financing_balance_change_5d_pct": _pct_change(
                        _safe_float(latest.get("融资余额")),
                        _safe_float(prev.get("融资余额")),
                    ),
                    "recent": _records(tail, 5),
                }
        except Exception as exc:
            result["errors"].append(f"两融数据: {exc}")

        # 深市两融（补充沪市）
        try:
            margin_sz = ak.macro_china_market_margin_sz()
            if margin_sz is not None and not margin_sz.empty:
                tail = margin_sz.tail(5)
                result["margin_trend_sz"] = {
                    "latest": _df_row_to_dict(tail.iloc[-1]),
                    "recent": _records(tail, 5),
                }
        except Exception as exc:
            result["errors"].append(f"深市两融: {exc}")

        try:
            nb = ak.stock_hsgt_fund_flow_summary_em()
            result["northbound_summary"] = _records(nb, self.max_items)
        except Exception as exc:
            result["errors"].append(f"北向汇总: {exc}")

        if not result["northbound_summary"]:
            # 回退：北向历史净买入
            try:
                nb_hist = ak.stock_hsgt_hist_em(symbol="北向资金")
                if nb_hist is not None and not nb_hist.empty:
                    result["northbound_summary"] = _records(nb_hist.tail(self.max_items), self.max_items)
                    result["northbound_source"] = "hsgt_hist_em"
            except Exception as exc:
                result["errors"].append(f"北向历史回退: {exc}")

        result["northbound_freshness"] = _northbound_freshness(result["northbound_summary"], self.as_of)
        if result["northbound_freshness"].get("stale"):
            days = result["northbound_freshness"].get("staleness_days")
            result["errors"].append(f"northbound_stale:{days}d")

        hot = self._get_hot_rank_df()
        if not hot.empty:
            result["market_hot_rank"] = _records(hot, min(20, self.max_items * 2))
            result["hot_rank_source"] = self._hot_rank_source
            if self._hot_rank_source and self._hot_rank_source != "em":
                result["errors"].append(f"hot_rank_fallback:{self._hot_rank_source}")
        elif self._hot_rank_error:
            result["errors"].append(f"人气榜: {self._hot_rank_error}")

        summary_df, flow_source, flow_errors = self._get_sector_summary()
        result["errors"].extend(flow_errors)
        if not summary_df.empty:
            result["sector_money_flow"] = build_sector_money_flow(summary_df, limit=10)
            result["sector_money_flow_source"] = flow_source
            if flow_source != "ths_summary":
                result["errors"].append(f"sector_money_flow_fallback:{flow_source}")
        elif flow_errors:
            result["errors"].append("sector_money_flow_all_sources_failed")

        rss_bundle = self._get_rss_bundle()
        result["rss_telegraph"] = rss_bundle.get("cls_telegraph") or []
        result["rss_important"] = rss_bundle.get("cls_telegraph_important") or []
        result["rss_feeds"] = rss_bundle.get("feeds") or []
        result["errors"].extend(rss_bundle.get("errors") or [])

        if result.get("policy_news_stale") or not result.get("policy_news"):
            policy_pool: list[dict[str, Any]] = []
            policy_pool.extend(result.get("global_news") or [])
            policy_pool.extend(result.get("global_news_sina") or [])
            policy_pool.extend(result.get("rss_important") or [])
            policy_pool.extend(result.get("rss_telegraph") or [])
            extracted = _extract_policy_news_from_pool(
                policy_pool,
                as_of=self.as_of,
                lookback_days=self.news_lookback_days,
                limit=self.max_items,
            )
            if extracted:
                result["policy_news"] = extracted
                result["policy_news_source"] = "rss_global_extract"
                result.pop("policy_news_stale", None)
                result["errors"] = [
                    e for e in result["errors"]
                    if e not in ("policy_news_stale_fallback", "policy_news_stale_or_empty")
                ]

        if not result["tushare_macro_news"]:
            fallback = _merge_macro_news_fallback(result, self.max_items)
            if fallback:
                result["tushare_macro_news"] = fallback
                result["tushare_macro_backfill"] = True
                result["errors"].append("tushare_macro_backfill_from_alt_sources")

        # 主线池：政策/联播/宏观；噪声池：电报/全球碎资讯（仅事件/拥挤旁路）
        mainline_pool: list[dict[str, Any]] = []
        mainline_pool.extend(result.get("policy_news") or [])
        mainline_pool.extend(result.get("tushare_macro_news") or [])
        noise_pool: list[dict[str, Any]] = []
        noise_pool.extend(result.get("global_news") or [])
        noise_pool.extend(result.get("global_news_sina") or [])
        noise_pool.extend(result.get("rss_important") or [])
        noise_pool.extend((result.get("rss_telegraph") or [])[: self.max_items])

        macro_news_pool: list[dict[str, Any]] = []
        macro_news_pool.extend(mainline_pool)
        macro_news_pool.extend(noise_pool)

        if self.config.sentiment.enabled:
            sent_cfg = self.config.sentiment
            mainline_only = bool(getattr(sent_cfg, "mainline_pool_only", True))
            score_pool = mainline_pool if mainline_only and mainline_pool else macro_news_pool
            result["sentiment_overview"] = self.scorer.score_news_items(score_pool)
            if isinstance(result["sentiment_overview"], dict):
                result["sentiment_overview"]["pool"] = (
                    "mainline" if mainline_only and mainline_pool else "all"
                )
                result["sentiment_overview"]["role"] = str(
                    getattr(sent_cfg, "role", "crowding_only") or "crowding_only"
                )
            if noise_pool:
                result["sentiment_noise"] = self.scorer.score_news_items(noise_pool)
                if isinstance(result["sentiment_noise"], dict):
                    result["sentiment_noise"]["pool"] = "noise"
                    result["sentiment_noise"]["plain_note"] = (
                        "电报/全球碎资讯噪声池：仅事件扫描与拥挤旁路，不进综合舆情主分"
                    )

        # S11：数库新闻情绪指数（市场温度旁路，失败不阻断）
        try:
            scope_df = ak.index_news_sentiment_scope()
            scope_recs = _records(scope_df.tail(260), 260) if scope_df is not None and not scope_df.empty else []
            result["market_news_sentiment_scope"] = build_market_news_sentiment_scope(
                scope_recs, as_of=self.as_of
            )
            if not result["market_news_sentiment_scope"].get("ok"):
                result["errors"].append("market_news_sentiment_scope_empty")
        except Exception as exc:
            result["market_news_sentiment_scope"] = {
                "ok": False,
                "source": "chinascope_akshare",
                "plain_note": "全市场新闻情绪温度计；仅作 A1 旁路，不进个股打分/不抬买入分",
                "error": str(exc)[:200],
            }
            result["errors"].append(f"market_news_sentiment_scope: {exc}")

        sector_names = list(
            dict.fromkeys(
                list(self.config.watch_sectors or [])
                + _sector_names_from_money_flow(result.get("sector_money_flow") or {})
            )
        )
        if sector_names and macro_news_pool and self.config.sentiment.enabled:
            result["industry_sentiment_index"] = build_industry_sentiment_index(
                macro_news_pool,
                sector_names,
                scorer=self.scorer,
            )

        result["macro_event_signals"] = build_macro_event_signals(result)

        return result

    def fetch_sector_intelligence(self, sector_name: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "sector": sector_name,
            "as_of": self.as_of.isoformat(),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "related_news": [],
            "rss_matches": [],
            "tushare_news": [],
            "hot_rank_mentions": [],
            "sector_flow_rank": {},
            "sentiment_analysis": {},
            "errors": [],
        }

        news_symbol = sector_name
        summary_df, _, summary_errors = self._get_sector_summary()
        result["errors"].extend(summary_errors)
        if not summary_df.empty and "板块" in summary_df.columns:
            matched_board = _match_board_name(summary_df["板块"], sector_name)
            if matched_board:
                news_symbol = matched_board

        try:
            news = ak.stock_news_em(symbol=news_symbol)
            result["related_news"] = filter_records_by_date(
                _records(news, self.max_items), self.as_of, lookback_days=self.news_lookback_days
            )
        except Exception as exc:
            result["errors"].append(f"板块新闻({news_symbol}): {exc}")

        rss_bundle = self._get_rss_bundle()
        if self.rss:
            combined = rss_bundle.get("combined") or []
            result["rss_matches"] = self.rss.filter_by_keywords(combined, [sector_name], self.max_items)

        if self.tushare and self.tushare.available:
            ts_news = self.tushare.fetch_sector_news(sector_name, limit=self.max_items)
            result["tushare_news"] = filter_records_by_date(
                ts_news.get("items") or [], self.as_of, lookback_days=self.news_lookback_days
            )
            result["errors"].extend(ts_news.get("errors") or [])

        hot = self._get_hot_rank_df()
        if not hot.empty:
            result["hot_rank_mentions"] = _records(hot.head(30), 30)
        elif self._hot_rank_error:
            result["errors"].append(f"热点榜: {self._hot_rank_error}")

        if not summary_df.empty and "板块" in summary_df.columns:
            matched_board = _match_board_name(summary_df["板块"], sector_name)
            if matched_board:
                row = summary_df[summary_df["板块"] == matched_board].iloc[0]
                ranked_chg = summary_df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
                rank_chg = int(ranked_chg.index[ranked_chg["板块"] == matched_board][0] + 1)
                rank_inflow = None
                if "净流入" in summary_df.columns:
                    ranked_flow = summary_df.sort_values("净流入", ascending=False).reset_index(drop=True)
                    rank_inflow = int(ranked_flow.index[ranked_flow["板块"] == matched_board][0] + 1)
                result["sector_flow_rank"] = {
                    "board": matched_board,
                    "rank_by_change": rank_chg,
                    "rank_by_inflow": rank_inflow,
                    "total_sectors": len(summary_df),
                    "note": "涨跌幅排名≠资金流入确认",
                    "snapshot": _df_row_to_dict(row),
                }

        if self.config.sentiment.enabled:
            pool = result["related_news"] + result["rss_matches"] + result["tushare_news"]
            result["sentiment_analysis"] = self.scorer.score_for_entity(pool, [sector_name], self.max_items * 2)
            sa_agg = (result["sentiment_analysis"] or {}).get("aggregate") or {}
            if sa_agg.get("event_distribution"):
                result["sector_event_signals"] = {
                    "event_distribution": sa_agg.get("event_distribution"),
                    "extreme": sa_agg.get("extreme"),
                    "dominant_tags": list((sa_agg.get("event_distribution") or {}).keys())[:5],
                }

        hot_records = result.get("hot_rank_mentions") or _records(hot, 30) if not hot.empty else []
        result["crowding_hint"] = assess_sector_crowding(sector_name, hot_rank_records=hot_records)

        return result

    def fetch_stock_intelligence(self, code: str, stock_name: str | None = None) -> dict[str, Any]:
        code = normalize_code(code)
        keywords = [code]
        if stock_name:
            keywords.append(stock_name)

        result: dict[str, Any] = {
            "code": code,
            "as_of": self.as_of.isoformat(),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "news": [],
            "research_reports": [],
            "sentiment_scores": {},
            "sentiment_analysis": {},
            "participation_desire": [],
            "xueqiu_hot": {},
            "market_comment": {},
            "lhb_records": [],
            "margin_detail": [],
            "rss_matches": [],
            "tushare": {},
            "northbound_hold": {},
            "errors": [],
        }

        try:
            news = ak.stock_news_em(symbol=code)
            result["news"] = filter_records_by_date(
                _records(news, self.max_items), self.as_of, lookback_days=self.news_lookback_days
            )
        except Exception as exc:
            result["errors"].append(f"个股新闻: {exc}")

        try:
            reports = ak.stock_research_report_em(symbol=code)
            matched = _filter_df(reports, code)
            result["research_reports"] = _records(matched, self.max_items)
        except Exception as exc:
            result["errors"].append(f"研报: {exc}")

        try:
            comments = self._get_comment_df()
            matched = _filter_df(comments, code)
            if not matched.empty:
                row = _df_row_to_dict(matched.iloc[0])
                result["market_comment"] = row
                if not stock_name and row.get("名称"):
                    keywords.append(str(row["名称"]))
        except Exception as exc:
            result["errors"].append(f"千股千评: {exc}")

        try:
            scores = ak.stock_comment_detail_zhpj_lspf_em(symbol=code)
            if not scores.empty:
                result["sentiment_scores"]["history_rating"] = _records(scores.tail(10), 10)
                result["sentiment_scores"]["latest_rating"] = _safe_float(scores.iloc[-1].get("评分"))
        except Exception as exc:
            result["errors"].append(f"历史评分: {exc}")

        try:
            desire = ak.stock_comment_detail_scrd_desire_em(symbol=code)
            result["participation_desire"] = _records(desire, 5)
        except Exception as exc:
            result["errors"].append(f"参与意愿: {exc}")

        try:
            follow_df, deal_df = self._get_xueqiu_dfs()
            result["xueqiu_hot"] = build_xueqiu_hot_snapshot(follow_df, deal_df, code)
            if follow_df.empty and deal_df.empty:
                result["errors"].append("xueqiu_hot_empty")
        except Exception as exc:
            result["errors"].append(f"雪球热度: {exc}")

        try:
            lhb = ak.stock_lhb_detail_em(start_date=ymd(self.as_of, -30), end_date=ymd(self.as_of))
            matched = _filter_df(lhb, code)
            result["lhb_records"] = _records(matched, 5)
        except Exception as exc:
            result["errors"].append(f"龙虎榜: {exc}")

        try:
            for date_str in recent_weekdays(self.as_of, 3):
                try:
                    margin = ak.stock_margin_detail_sse(date=date_str)
                    matched = _filter_df(margin, code, ("标的证券代码",))
                    if not matched.empty:
                        result["margin_detail"].append(_df_row_to_dict(matched.iloc[0]))
                        break
                except Exception:
                    continue
        except Exception as exc:
            result["errors"].append(f"融资融券: {exc}")

        # 北向持股个股排行（合法 market=北向；失败写入可读原因，不抛裸 TypeError）
        try:
            hk = self._get_northbound_hold_df()
            if not hk.empty:
                matched = _filter_df(hk, code, ("代码", "股票代码"))
                if not matched.empty:
                    result["northbound_hold"] = _df_row_to_dict(matched.iloc[0])
            elif self._northbound_hold_error:
                result["errors"].append(f"北向持股: {self._northbound_hold_error}")
        except Exception as exc:
            result["errors"].append(f"北向持股: {exc}")

        rss_bundle = self._get_rss_bundle()
        if self.rss:
            combined = rss_bundle.get("combined") or []
            result["rss_matches"] = self.rss.filter_by_keywords(combined, keywords, self.max_items)

        if self.tushare and self.tushare.available:
            ts_bundle = self.tushare.fetch_stock_bundle(code, limit=self.max_items)
            result["tushare"] = ts_bundle
            result["errors"].extend(ts_bundle.get("errors") or [])

        # 股权质押比例（东财全市场表缓存查询）
        try:
            pledge_df = self._get_pledge_ratio_df()
            if not pledge_df.empty:
                matched = _filter_df(pledge_df, code, ("股票代码", "代码"))
                if not matched.empty:
                    row = _df_row_to_dict(matched.iloc[0])
                    result["pledge_ratio"] = {
                        "ratio": _safe_float(row.get("质押比例")),
                        "shares": row.get("质押股数"),
                        "market_value": row.get("质押市值"),
                        "trade_date": row.get("交易日期"),
                        "industry": row.get("所属行业"),
                        "source": "em_gpzy_pledge_ratio",
                    }
            elif self._pledge_ratio_error:
                result["errors"].append(f"股权质押: {self._pledge_ratio_error}")
        except Exception as exc:
            result["errors"].append(f"股权质押: {exc}")

        # 股东增减持（同花顺）；近窗减持进硬门禁
        try:
            holder_df = ak.stock_shareholder_change_ths(symbol=code)
            if holder_df is not None and not holder_df.empty:
                recs = _records(holder_df.head(12), 12)
                result["shareholder_changes"] = recs
                as_cutoff = self.as_of - timedelta(days=90)
                recent_reduce: list[dict[str, Any]] = []
                for item in recs:
                    if not isinstance(item, dict):
                        continue
                    text = str(item.get("变动数量") or "") + str(item.get("变动途径") or "")
                    if "减持" not in text:
                        continue
                    d = parse_record_date(
                        item, date_keys=("公告日期", "date", "日期", "变动期间")
                    )
                    if d is None or d >= as_cutoff:
                        recent_reduce.append(item)
                if recent_reduce:
                    result["recent_share_reduce"] = recent_reduce[:5]
        except Exception as exc:
            result["errors"].append(f"股东增减持: {exc}")

        if self.config.sentiment.enabled:
            pool: list[dict[str, Any]] = []
            pool.extend(result["news"])
            pool.extend(result["rss_matches"])
            pool.extend(result.get("tushare", {}).get("news") or [])
            pool.extend(result.get("tushare", {}).get("announcements") or [])
            result["sentiment_analysis"] = self.scorer.score_for_entity(pool, keywords, self.max_items * 3)

        hot = self._get_hot_rank_df()
        hot_records = _records(hot, 100) if not hot.empty else []
        result["crowding_signal"] = assess_stock_crowding(
            code,
            hot_rank_records=hot_records,
            market_comment=result.get("market_comment") or {},
            xueqiu_hot=result.get("xueqiu_hot") or {},
            participation_desire=result.get("participation_desire") or [],
        )

        return result


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 2)


def _news_title_key(item: dict[str, Any]) -> str:
    import re

    title = str(item.get("title") or item.get("标题") or item.get("新闻标题") or "").strip()
    return re.sub(r"\s+", "", title)[:80]


def _merge_macro_news_fallback(macro: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Tushare 宏观新闻不可用时，用东财/新浪/财联社/RSS 合并补位。"""
    pool: list[dict[str, Any]] = []
    for key in ("global_news", "global_news_sina", "rss_important", "rss_telegraph"):
        pool.extend(macro.get(key) or [])
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in pool:
        if not isinstance(item, dict):
            continue
        key = _news_title_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


_POLICY_NEWS_KEYWORDS = (
    "国务院", "央行", "证监会", "发改委", "财政部", "工信部", "商务部", "金融监管",
    "政治局", "中央经济工作会议", "国常会", "降准", "降息", "货币政策", "财政政策",
    "产业政策", "稳市", "回购", "增持", "监管", "立案", "国新", "汇金",
)


def _extract_policy_news_from_pool(
    pool: list[dict[str, Any]],
    *,
    as_of: date,
    lookback_days: int,
    limit: int,
) -> list[dict[str, Any]]:
    """联播/CCTV 陈旧或空时，从全球快讯/RSS 抽取政策导向标题（无额外 API）。"""
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for item in pool:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("标题") or item.get("新闻标题") or "").strip()
        content = str(item.get("content") or item.get("内容") or item.get("summary") or "")
        text = f"{title} {content}"
        if not title or not any(kw in text for kw in _POLICY_NEWS_KEYWORDS):
            continue
        key = _news_title_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(item)
    if not candidates:
        return []
    return filter_records_by_date(candidates, as_of, lookback_days=lookback_days)[:limit]


_MACRO_CAL_LABELS = {
    "pmi": "中国制造业PMI",
    "cpi": "中国CPI",
    "m2": "中国货币供应M2",
}


def _pick_latest_macro_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从宏观序列中选取最新一期（兼容降序/乱序）。"""
    best: dict[str, Any] | None = None
    best_period: date | None = None
    for rec in records:
        if not isinstance(rec, dict):
            continue
        period = parse_macro_period_date(rec)
        if period is None:
            continue
        if best_period is None or period > best_period:
            best_period = period
            best = rec
    if best is not None:
        return best
    for rec in records:
        if isinstance(rec, dict):
            return rec
    return None


def _macro_hard_echo_from_macro(macro_hard: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    """已公布 PMI/CPI/M2 快照（背景），不得当作未来经济日历。"""
    events: list[dict[str, Any]] = []
    for key, label in _MACRO_CAL_LABELS.items():
        records = macro_hard.get(key) or []
        if not records:
            continue
        latest = _pick_latest_macro_record(records)
        if not isinstance(latest, dict):
            continue
        period = parse_macro_period_date(latest)
        period_label = str(latest.get("月份") or latest.get("month") or "").strip()
        if period is not None:
            date_str = period.strftime("%Y-%m")
        elif period_label:
            date_str = period_label
        else:
            date_str = as_of.isoformat()
        events.append(
            {
                "日期": date_str,
                "event": label,
                "period_label": period_label or None,
                "snapshot": latest,
                "source": "macro_hard_echo",
                "kind": "published_background",
                "note": "已公布硬指标回看，不是未来发布日程",
            }
        )
    return events


# 兼容旧测试/调用名
def _synthetic_calendar_from_macro_hard(macro_hard: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    return _macro_hard_echo_from_macro(macro_hard, as_of)


def _trading_days_between(start: date, end: date) -> int:
    """start 与 end 之间的工作日数（不含 start，含 end）。"""
    if start >= end:
        return 0
    days = 0
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if cursor.weekday() < 5:
            days += 1
        cursor += timedelta(days=1)
    return days


def _sector_names_from_money_flow(flow: dict[str, Any], limit: int = 6) -> list[str]:
    """从板块资金流摘要提取自动扩板块候选名。"""
    names: list[str] = []
    for key in ("top_inflow", "top_gainers"):
        for row in flow.get(key) or []:
            if not isinstance(row, dict):
                continue
            board = str(row.get("板块") or row.get("名称") or row.get("board") or "").strip()
            if board and board not in names:
                names.append(board)
            if len(names) >= limit:
                return names
    return names


def _northbound_freshness(summary: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    """检测北向数据是否滞后（按交易日计，避免周末误判）。"""
    if not summary:
        return {"stale": True, "staleness_days": None, "latest_date": None}
    latest_row = summary[-1]
    latest_date = parse_record_date(latest_row)
    if latest_date is None:
        return {"stale": False, "staleness_days": None, "latest_date": None, "note": "date_unparsed"}
    calendar_gap = (as_of - latest_date).days
    trading_gap = _trading_days_between(latest_date, as_of)
    # 允许 2 个交易日缓冲（节假日/台风停市等）
    stale = trading_gap > 2
    note = None
    if stale and calendar_gap <= 3:
        note = "trading_pause_likely"
    return {
        "stale": stale,
        "staleness_days": calendar_gap,
        "trading_staleness_days": trading_gap,
        "latest_date": latest_date.isoformat(),
        "note": note,
    }
