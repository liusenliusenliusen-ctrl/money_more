"""雪球社交热度、参与意愿与宏观事件信号单测（无网络）。"""

from __future__ import annotations

import pandas as pd

from money_more.analysis.factor_scorecard import build_stock_scorecard
from money_more.analysis.sentiment import (
    assess_stock_crowding,
    build_macro_event_signals,
)
from money_more.data.fetcher import build_xueqiu_hot_snapshot


def test_build_xueqiu_hot_snapshot_includes_rank() -> None:
    follow = pd.DataFrame(
        [
            {"股票代码": "SH300750", "股票简称": "宁德时代", "关注": 900, "最新价": 200},
            {"股票代码": "SH600519", "股票简称": "贵州茅台", "关注": 100, "最新价": 1400},
        ]
    )
    deal = pd.DataFrame(
        [
            {"股票代码": "SH300750", "股票简称": "宁德时代", "关注": 50, "最新价": 200},
            {"股票代码": "SH600519", "股票简称": "贵州茅台", "关注": 10, "最新价": 1400},
        ]
    )
    snap = build_xueqiu_hot_snapshot(follow, deal, "300750")
    assert snap["follow"]["排名"] == 1
    assert snap["deal"]["排名"] == 1
    assert snap["follow"]["榜单"] == "follow"


def test_crowding_uses_xueqiu_and_participation_desire() -> None:
    sig = assess_stock_crowding(
        "300750",
        xueqiu_hot={"deal": {"排名": 5}, "follow": {"排名": 8}},
        participation_desire=[{"参与意愿": 78, "参与意愿变化": 15}],
    )
    assert sig["crowding_risk"] == "high"
    assert sig["crowding_score"] >= 4
    assert any("雪球" in s for s in sig["signals"])
    assert any("参与意愿" in s for s in sig["signals"])


def test_build_macro_event_signals_merges_calendar() -> None:
    macro = {
        "sentiment_overview": {
            "aggregate": {
                "event_distribution": {"earnings_positive": 3, "macro_negative": 2},
                "extreme": None,
            }
        },
        "economic_calendar": [{"日期": "2026-07-25", "event": "美国PCE"}],
    }
    out = build_macro_event_signals(macro)
    assert out["dominant_tags"][0] == "earnings_positive"
    assert len(out["watchlist"]) >= 3
    assert any(w.get("source") == "economic_calendar" for w in out["watchlist"])
    assert any(w.get("event") == "业绩预增/扭亏" for w in out["watchlist"])


def test_scorecard_blends_participation_desire() -> None:
    card = build_stock_scorecard(
        {"history": {}, "quote": {}},
        {},
        {
            "sentiment_analysis": {"aggregate": {"score_100": 60}},
            "participation_desire": [{"参与意愿": 80}],
            "xueqiu_hot": {"deal": {"排名": 5}},
            "crowding_signal": {"crowding_risk": "low"},
        },
    )
    assert card["scores"]["sentiment"] > 60
    assert any("参与意愿" in e for e in card["evidence"]["sentiment"])
