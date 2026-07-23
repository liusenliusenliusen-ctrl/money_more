"""人气榜多源回退与拥挤度量化单测（无网络）。"""

from __future__ import annotations

import pandas as pd
import pytest

from money_more.analysis.factor_scorecard import build_stock_scorecard
from money_more.analysis.sentiment import (
    FinancialSentimentScorer,
    assess_sector_crowding,
    assess_stock_crowding,
)
from money_more.data.fetcher import _hot_rank_from_xueqiu_follow, fetch_hot_rank_with_fallback


def test_hot_rank_from_xueqiu_follow_normalizes_columns() -> None:
    raw = pd.DataFrame(
        [
            {"股票代码": "SH600519", "股票简称": "贵州茅台", "关注": 100, "最新价": 1400},
            {"股票代码": "SZ300750", "股票简称": "宁德时代", "关注": 500, "最新价": 200},
        ]
    )
    out = _hot_rank_from_xueqiu_follow(raw, limit=10)
    assert list(out["当前排名"]) == [1, 2]
    assert out.iloc[0]["股票名称"] == "宁德时代"
    assert out.iloc[0]["代码"] == "SZ300750"


def test_fetch_hot_rank_falls_back_to_xueqiu(monkeypatch: pytest.MonkeyPatch) -> None:
    def _em_fail() -> pd.DataFrame:
        raise ConnectionError("push2 proxy")

    def _xq_ok() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"股票代码": "SH601899", "股票简称": "紫金矿业", "关注": 900, "最新价": 18.0},
                {"股票代码": "SH600519", "股票简称": "贵州茅台", "关注": 100, "最新价": 1400},
            ]
        )

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_hot_rank_em", _em_fail)
    monkeypatch.setattr("money_more.data.fetcher.ak.stock_hot_follow_xq", _xq_ok)

    df, source, warnings = fetch_hot_rank_with_fallback(limit=5)
    assert source == "xueqiu_follow"
    assert len(df) == 2
    assert df.iloc[0]["股票名称"] == "紫金矿业"
    assert any("hot_rank_fallback:xueqiu_follow" in w for w in warnings)


def test_sentiment_aggregate_extreme_and_events() -> None:
    scorer = FinancialSentimentScorer()
    items = [
        {"title": "业绩预增超预期，回购增持"},
        {"title": "业绩预增创新高，景气回暖"},
        {"title": "中标扩产，净利润大增"},
    ]
    out = scorer.score_news_items(items)
    agg = out["aggregate"]
    assert agg["count"] == 3
    assert agg.get("event_distribution")
    assert agg["positive_ratio"] >= 0.9
    assert agg.get("extreme") in (None, "euphoria")


def test_assess_stock_crowding_from_hot_and_comment() -> None:
    hot = [
        {"当前排名": 3, "代码": "SH300750", "股票名称": "宁德时代"},
        {"当前排名": 10, "代码": "SH600519", "股票名称": "贵州茅台"},
    ]
    sig = assess_stock_crowding(
        "300750",
        hot_rank_records=hot,
        market_comment={"关注指数": 93.5},
    )
    assert sig["crowding_risk"] == "high"
    assert sig["crowding_score"] >= 4
    assert any("Top3" in s for s in sig["signals"])


def test_assess_sector_crowding_counts_mapped_leaders() -> None:
    hot = [
        {"当前排名": 1, "代码": "SH600519", "股票名称": "贵州茅台"},
        {"当前排名": 2, "代码": "SZ300750", "股票名称": "宁德时代"},
        {"当前排名": 5, "代码": "SH601398", "股票名称": "工商银行"},
    ]
    liquor = assess_sector_crowding("白酒", hot_rank_records=hot)
    assert liquor["hot_hits"] == 1
    assert liquor["crowding_risk"] == "medium"

    new_energy = assess_sector_crowding("新能源", hot_rank_records=hot)
    assert new_energy["hot_hits"] == 1


def test_scorecard_applies_crowding_penalty() -> None:
    base = build_stock_scorecard(
        {"history": {}, "quote": {}},
        {},
        {"sentiment_analysis": {"aggregate": {"score_100": 70}}},
    )
    crowded = build_stock_scorecard(
        {"history": {}, "quote": {}},
        {},
        {
            "sentiment_analysis": {"aggregate": {"score_100": 70}},
            "crowding_signal": {"crowding_risk": "high", "crowding_score": 5},
        },
    )
    assert crowded["scores"]["sentiment"] < base["scores"]["sentiment"]
    assert any("拥挤" in e for e in crowded["evidence"]["sentiment"])
