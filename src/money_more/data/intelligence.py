from __future__ import annotations

from datetime import date, datetime
from typing import Any

import akshare as ak
import pandas as pd

from money_more.analysis.sentiment import FinancialSentimentScorer
from money_more.config import AppConfig
from money_more.data.as_of import (
    filter_calendar_upcoming,
    filter_records_by_date,
    parse_as_of,
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
    fetch_sector_board_summary,
    normalize_code,
)
from money_more.data.rss_feeds import RssFeedFetcher
from money_more.data.tushare_source import TushareSource


def _records(df: pd.DataFrame | None, limit: int = 10) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return df.head(limit).to_dict(orient="records")


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
            cls_direct=config.rss.cls_direct,
            use_fallback_rss=config.rss.use_fallback_rss,
        ) if config.rss.enabled else None
        self._rss_cache: dict[str, Any] | None = None
        self._tushare_macro_cache: dict[str, Any] | None = None
        self._comment_df: pd.DataFrame | None = None
        self._hot_rank_df: pd.DataFrame | None = None
        self._hot_rank_error: str | None = None
        self._sector_summary_cache: tuple[pd.DataFrame, str, list[str]] | None = None

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
        self._sector_summary_cache = None

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
        try:
            self._hot_rank_df = ak.stock_hot_rank_em()
        except Exception as exc:
            self._hot_rank_df = pd.DataFrame()
            self._hot_rank_error = str(exc)
        return self._hot_rank_df

    def _get_sector_summary(self) -> tuple[pd.DataFrame, str, list[str]]:
        if self._sector_summary_cache is not None:
            return self._sector_summary_cache
        self._sector_summary_cache = fetch_sector_board_summary()
        return self._sector_summary_cache

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
            self._tushare_macro_cache = {"items": [], "errors": ["Tushare 未配置"]}
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

        # 宏观硬数据：PMI / CPI（失败则跳过）
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
                    macro_hard[label] = _records(df.tail(6), 6)
            except Exception as exc:
                result["errors"].append(f"宏观{label}: {exc}")
        result["macro_hard"] = macro_hard

        # 全球流动性硬指标（美债 + USD/CNY）——主线宏观外因
        try:
            result["global_liquidity"] = fetch_global_liquidity(self.as_of)
            result["errors"].extend(result["global_liquidity"].get("errors") or [])
        except Exception as exc:
            result["errors"].append(f"global_liquidity: {exc}")
            result["global_liquidity"] = {"stance": "unknown", "errors": [str(exc)]}

        if not result["economic_calendar"] and macro_hard:
            synth = _synthetic_calendar_from_macro_hard(macro_hard, self.as_of)
            if synth:
                result["economic_calendar"] = synth[: self.max_items]
                result["economic_calendar_synthetic"] = True

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

        if not result["tushare_macro_news"]:
            fallback = _merge_macro_news_fallback(result, self.max_items)
            if fallback:
                result["tushare_macro_news"] = fallback
                result["tushare_macro_backfill"] = True
                result["errors"].append("tushare_macro_backfill_from_alt_sources")

        if self.config.sentiment.enabled:
            macro_news_pool: list[dict[str, Any]] = []
            macro_news_pool.extend(result["policy_news"])
            macro_news_pool.extend(result["global_news"])
            macro_news_pool.extend(result.get("global_news_sina") or [])
            macro_news_pool.extend(result["rss_important"])
            macro_news_pool.extend(result["rss_telegraph"][: self.max_items])
            macro_news_pool.extend(result["tushare_macro_news"])
            result["sentiment_overview"] = self.scorer.score_news_items(macro_news_pool)

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
                ranked = summary_df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
                rank_pos = ranked.index[ranked["板块"] == matched_board][0] + 1
                result["sector_flow_rank"] = {
                    "board": matched_board,
                    "rank_by_change": int(rank_pos),
                    "total_sectors": len(summary_df),
                    "snapshot": _df_row_to_dict(row),
                }

        if self.config.sentiment.enabled:
            pool = result["related_news"] + result["rss_matches"] + result["tushare_news"]
            result["sentiment_analysis"] = self.scorer.score_for_entity(pool, [sector_name], self.max_items * 2)

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
            follow = ak.stock_hot_follow_xq()
            deal = ak.stock_hot_deal_xq()
            f_row = _filter_df(follow, code, ("股票代码",))
            d_row = _filter_df(deal, code, ("股票代码",))
            result["xueqiu_hot"] = {
                "follow": _df_row_to_dict(f_row.iloc[0]) if not f_row.empty else {},
                "deal": _df_row_to_dict(d_row.iloc[0]) if not d_row.empty else {},
            }
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

        # 北向持股（若接口可用）
        try:
            hk = ak.stock_hsgt_hold_stock_em(market="北向持股")
            matched = _filter_df(hk, code)
            if not matched.empty:
                result["northbound_hold"] = _df_row_to_dict(matched.iloc[0])
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

        if self.config.sentiment.enabled:
            pool: list[dict[str, Any]] = []
            pool.extend(result["news"])
            pool.extend(result["rss_matches"])
            pool.extend(result.get("tushare", {}).get("news") or [])
            pool.extend(result.get("tushare", {}).get("announcements") or [])
            result["sentiment_analysis"] = self.scorer.score_for_entity(pool, keywords, self.max_items * 3)

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


_MACRO_CAL_LABELS = {
    "pmi": "中国制造业PMI",
    "cpi": "中国CPI",
    "m2": "中国货币供应M2",
}


def _synthetic_calendar_from_macro_hard(macro_hard: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    """主经济日历为空时，用 PMI/CPI/M2 最新发布构造宏观事件锚。"""
    events: list[dict[str, Any]] = []
    month_keys = ("月份", "month", "日期", "date", "时间", "公布时间")
    for key, label in _MACRO_CAL_LABELS.items():
        records = macro_hard.get(key) or []
        if not records:
            continue
        latest = records[-1]
        if not isinstance(latest, dict):
            continue
        date_str = None
        for mk in month_keys:
            if mk in latest and latest.get(mk):
                date_str = str(latest[mk])
                break
        events.append(
            {
                "日期": date_str or as_of.isoformat(),
                "event": label,
                "snapshot": latest,
                "source": "macro_hard",
            }
        )
    return events


def _northbound_freshness(summary: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    """检测北向数据是否滞后（节假日/接口空窗）。"""
    if not summary:
        return {"stale": True, "staleness_days": None, "latest_date": None}
    latest_row = summary[-1]
    latest_date = parse_record_date(latest_row)
    if latest_date is None:
        return {"stale": False, "staleness_days": None, "latest_date": None, "note": "date_unparsed"}
    staleness = (as_of - latest_date).days
    # 允许周末 + 1 个交易日缓冲
    stale = staleness > 3
    return {
        "stale": stale,
        "staleness_days": staleness,
        "latest_date": latest_date.isoformat(),
    }
