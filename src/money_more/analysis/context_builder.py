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
        "economic_calendar": (macro.get("economic_calendar") or [])[:5],
        "policy_news": (macro.get("policy_news") or [])[:max_news],
        "global_news": (macro.get("global_news") or [])[:max_news],
        "rss_important": (macro.get("rss_important") or [])[:max_news],
        "rss_telegraph": (macro.get("rss_telegraph") or [])[:max_news],
        "tushare_macro_news": (macro.get("tushare_macro_news") or [])[:max_news],
        "market_hot_rank": (macro.get("market_hot_rank") or [])[:10],
        "errors": (macro.get("errors") or [])[:8],
    }
    return out


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
            "market_comment": intel.get("market_comment"),
            "research_reports": (intel.get("research_reports") or [])[:3],
            "rss_matches": (intel.get("rss_matches") or [])[:4],
            "tushare": {
                "valuation": ts.get("valuation"),
                "announcements": (ts.get("announcements") or [])[:4],
                "forecast": (ts.get("forecast") or [])[:3],
                "share_float": (ts.get("share_float") or [])[:3],
                "financials": {
                    "indicators": ((ts.get("financials") or {}).get("indicators") or [])[:2],
                },
                "errors": (ts.get("errors") or [])[:5],
            },
            "errors": (intel.get("errors") or [])[:5],
        },
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
    out = {}
    for k, v in hard.items():
        if isinstance(v, list):
            out[k] = v[-3:]
        else:
            out[k] = v
    return out


def _keep_keys(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: d[k] for k in keys if k in d}


def _trim_dict(d: dict[str, Any], n: int) -> dict[str, Any]:
    items = list(d.items())[:n]
    return dict(items)
