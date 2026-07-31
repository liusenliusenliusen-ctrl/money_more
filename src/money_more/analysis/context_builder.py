"""压缩送入 LLM 的上下文，降低截断与噪声。"""

from __future__ import annotations

from typing import Any


def compact_macro_intel(macro: dict[str, Any], max_news: int = 6) -> dict[str, Any]:
    if not macro:
        return {}
    out = {
        "as_of": macro.get("as_of"),
        "sentiment_overview": macro.get("sentiment_overview"),
        "margin_trend": _keep_keys(macro.get("margin_trend") or {}, ("financing_balance_change_5d_pct", "latest")),
        "northbound_summary": (macro.get("northbound_summary") or [])[:3],
        "sector_money_flow": _compact_sector_flow(macro.get("sector_money_flow") or {}),
        "macro_hard": _tail_macro_hard(macro.get("macro_hard") or {}),
        "global_liquidity": _compact_global_liquidity(macro.get("global_liquidity") or {}),
        "economic_calendar": (macro.get("economic_calendar") or [])[:5],
        "economic_calendar_synthetic": macro.get("economic_calendar_synthetic"),
        "northbound_freshness": macro.get("northbound_freshness"),
        "policy_news": (macro.get("policy_news") or [])[:max_news],
        "global_news": (macro.get("global_news") or [])[:max_news],
        "rss_important": (macro.get("rss_important") or [])[:max_news],
        "rss_telegraph": (macro.get("rss_telegraph") or [])[:max_news],
        "tushare_macro_news": (macro.get("tushare_macro_news") or [])[:max_news],
        "tushare_macro_backfill": macro.get("tushare_macro_backfill"),
        "market_hot_rank": (macro.get("market_hot_rank") or [])[:10],
        "macro_event_signals": _compact_macro_events(macro.get("macro_event_signals") or {}),
        "industry_sentiment_index": _compact_industry_sentiment(macro.get("industry_sentiment_index") or {}),
        "narrative_radar": _compact_narrative_radar(macro.get("narrative_radar") or {}),
        "errors": (macro.get("errors") or [])[:8],
    }
    return out


def _compact_macro_events(signals: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(signals, dict) or not signals:
        return {}
    return {
        "extreme": signals.get("extreme"),
        "dominant_tags": (signals.get("dominant_tags") or [])[:5],
        "watchlist": (signals.get("watchlist") or [])[:6],
    }


def _compact_industry_sentiment(index: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(index, dict) or not index:
        return {}
    sectors = []
    for row in (index.get("sectors") or [])[:8]:
        if not isinstance(row, dict):
            continue
        sectors.append(
            {
                "sector": row.get("sector"),
                "score_100": row.get("score_100"),
                "label": row.get("label"),
                "count": row.get("count"),
                "extreme": row.get("extreme"),
            }
        )
    return {"sectors": sectors, "note": index.get("note")}


def _compact_narrative_radar(radar: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(radar, dict) or not radar:
        return {}
    tracks = []
    for t in radar.get("tracks") or []:
        if not isinstance(t, dict):
            continue
        tracks.append(
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "source_type": t.get("source_type"),
                "signal_strength": t.get("signal_strength"),
                "hit_count": t.get("hit_count"),
                "evidence_snippets": (t.get("evidence_snippets") or [])[:3],
            }
        )
    pol = radar.get("policy_market_hypothesis") or {}
    return {
        "plain_note": radar.get("plain_note"),
        "active_track_ids": radar.get("active_track_ids") or [],
        "tracks": tracks,
        "policy_market_hypothesis": {
            "id": pol.get("id"),
            "title": pol.get("title"),
            "status": pol.get("status"),
            "thesis": (pol.get("thesis") or "")[:220],
            "entry_conditions": (pol.get("entry_conditions") or [])[:3],
            "falsify_signals": (pol.get("falsify_signals") or [])[:3],
            "observe_metrics": (pol.get("observe_metrics") or [])[:3],
            "if_true_portfolio_implication": pol.get("if_true_portfolio_implication"),
            "evidence_now": (pol.get("evidence_now") or [])[:3],
            "note": pol.get("note"),
        },
    }


def compact_stock_snap(snap: dict[str, Any]) -> dict[str, Any]:
    if not snap:
        return {}
    intel = snap.get("intelligence") or {}
    ts = intel.get("tushare") or {}
    return {
        "code": snap.get("code"),
        "as_of": snap.get("as_of"),
        "quote": _trim_dict(snap.get("quote") or {}, 12),
        "history": snap.get("history") or {},
        "fund_flow": _keep_keys(snap.get("fund_flow") or {}, ("net_3d", "net_5d", "net_20d")),
        "financial": {
            "abstract": ((snap.get("financial") or {}).get("abstract") or [])[:3],
        },
        "news": (snap.get("news") or [])[:4],
        "cross_check": snap.get("cross_check"),
        "hard_gates": snap.get("hard_gates"),
        "intelligence": {
            "sentiment_analysis": intel.get("sentiment_analysis"),
            "crowding_signal": intel.get("crowding_signal"),
            "market_comment": intel.get("market_comment"),
            "xueqiu_hot": intel.get("xueqiu_hot"),
            "participation_desire": (intel.get("participation_desire") or [])[-2:],
            "research_reports": (intel.get("research_reports") or [])[:3],
            "rss_matches": (intel.get("rss_matches") or [])[:4],
            "tushare": {
                "valuation": {
                    "latest": _compact_valuation_latest((ts.get("valuation") or {}).get("latest")),
                    "percentiles": (ts.get("valuation") or {}).get("percentiles"),
                },
                "announcements": (ts.get("announcements") or [])[:4],
                "forecast": (ts.get("forecast") or [])[:3],
                "share_float": (ts.get("share_float") or [])[:3],
                "financials": {
                    "indicators": ((ts.get("financials") or {}).get("indicators") or [])[:2],
                    "cashflow": ((ts.get("financials") or {}).get("cashflow") or [])[:2],
                },
                "errors": (ts.get("errors") or [])[:5],
            },
            "errors": (intel.get("errors") or [])[:5],
        },
        "earnings_revision": snap.get("earnings_revision"),
        "ocf_quality": snap.get("ocf_quality"),
        "info_completeness": snap.get("info_completeness"),
        "errors": (snap.get("errors") or [])[:5],
    }


def _compact_sector_flow(flow: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(flow, dict):
        return {}
    return {
        "top_gainers": (flow.get("top_gainers") or [])[:5],
        "top_losers": (flow.get("top_losers") or [])[:5],
        "top_inflow": (flow.get("top_inflow") or [])[:5],
    }


def _tail_macro_hard(hard: dict[str, Any]) -> dict[str, Any]:
    """macro_hard 序列来自 AkShare 降序，保留最新 3 期。"""
    out = {}
    for k, v in hard.items():
        if isinstance(v, list):
            out[k] = v[:3]
        else:
            out[k] = v
    return out


def _compact_global_liquidity(gl: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(gl, dict) or not gl:
        return {}
    eb = gl.get("equity_bond") or {}
    return {
        "stance": gl.get("stance"),
        "plain_note": gl.get("plain_note"),
        "a_share_implication": gl.get("a_share_implication"),
        "us_10y": gl.get("us_10y"),
        "us_2s10s": gl.get("us_2s10s"),
        "cn_10y": gl.get("cn_10y"),
        "usd_cny": gl.get("usd_cny"),
        "us_cn_10y_spread_bp": gl.get("us_cn_10y_spread_bp"),
        "equity_bond": {
            "ok": eb.get("ok"),
            "regime": eb.get("regime"),
            "erp_bp": eb.get("erp_bp"),
            "pe_ttm": eb.get("pe_ttm"),
            "earnings_yield_pct": eb.get("earnings_yield_pct"),
            "cn_10y_pct": eb.get("cn_10y_pct"),
            "implied_max_total_pct": eb.get("implied_max_total_pct"),
            "implied_min_cash_pct": eb.get("implied_min_cash_pct"),
            "note": eb.get("note"),
        }
        if eb
        else {},
        "signals": (gl.get("signals") or [])[:5],
        "series_tail": (gl.get("series_tail") or [])[-5:],
        "source": gl.get("source"),
    }


def _compact_valuation_latest(latest: Any) -> dict[str, Any]:
    if not isinstance(latest, dict) or not latest:
        return {}
    keys = (
        "pe",
        "pe_ttm",
        "pb",
        "dv_ratio",
        "dv_ttm",
        "close",
        "total_mv",
        "circ_mv",
        "turnover_rate",
        "trade_date",
    )
    return {k: latest[k] for k in keys if k in latest}


def _keep_keys(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: d[k] for k in keys if k in d}


def _trim_dict(d: dict[str, Any], n: int) -> dict[str, Any]:
    items = list(d.items())[:n]
    return dict(items)
