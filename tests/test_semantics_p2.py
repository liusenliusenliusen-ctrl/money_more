"""S11–S13：数库情绪指数旁路、负债/质押/减持门禁、报告分列。"""

from __future__ import annotations

from money_more.analysis.cross_check import apply_hard_gates
from money_more.analysis.factor_scorecard import build_stock_scorecard
from money_more.analysis.sentiment import build_market_news_sentiment_scope
from money_more.report.writer import render_daily_report


def test_build_market_news_sentiment_scope_labels() -> None:
    # 递增序列：最新最高 → hot
    hot_recs = [
        {"日期": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "市场情绪指数": 0.4 + i * 0.003, "沪深300指数": 4000.0}
        for i in range(80)
    ]
    out = build_market_news_sentiment_scope(hot_recs, as_of="2026-07-31")
    assert out["ok"] is True
    assert out["label"] == "hot"
    assert out["index"] is not None
    assert "不进个股打分" in out["plain_note"]

    # 递减序列：最新最低 → cold
    cold_recs = [
        {"日期": f"d{i:03d}", "市场情绪指数": 1.0 - i * 0.01, "沪深300指数": 4000}
        for i in range(50)
    ]
    cold = build_market_news_sentiment_scope(cold_recs)
    assert cold["ok"] is True
    assert cold["label"] == "cold"


def test_hard_gates_debt_pledge_reduce() -> None:
    # 高负债非金融 → force_watch
    debt = apply_hard_gates(
        "600519",
        {"as_of": "2026-07-01", "quote": {"名称": "贵州茅台"}, "history": {"volume": 100}},
        {"financials": {"indicators": [{"debt_to_assets": 78.0}]}},
    )
    assert debt["force_watch"] is True
    assert any("资产负债率" in r for r in debt["reasons"])

    # 金融业豁免 block
    bank = apply_hard_gates(
        "600000",
        {"as_of": "2026-07-01", "quote": {"名称": "浦发银行"}, "history": {"volume": 100}},
        {"financials": {"indicators": [{"debt_to_assets": 92.0}]}},
    )
    assert bank["block_buy"] is False
    assert any("金融业豁免" in r for r in bank["reasons"])

    # 高质押 → block
    pledge = apply_hard_gates(
        "600370",
        {
            "as_of": "2026-07-01",
            "quote": {"名称": "三房巷"},
            "history": {"volume": 100},
            "intelligence": {"pledge_ratio": {"ratio": 65.0, "industry": "化学纤维"}},
        },
        {},
    )
    assert pledge["block_buy"] is True
    assert any("质押" in r for r in pledge["reasons"])

    # 近窗减持
    reduce = apply_hard_gates(
        "000001",
        {
            "as_of": "2026-07-01",
            "quote": {"名称": "平安银行"},
            "history": {"volume": 100},
            "intelligence": {
                "recent_share_reduce": [{"变动股东": "某某", "变动数量": "减持100万", "公告日期": "2026-06-20"}]
            },
        },
        {},
    )
    assert reduce["force_watch"] is True
    assert any("减持" in r for r in reduce["reasons"])

    # 公告标题减持
    ann = apply_hard_gates(
        "000002",
        {"as_of": "2026-07-01", "quote": {"名称": "万科A"}, "history": {"volume": 100}},
        {"announcements": [{"title": "关于控股股东拟减持股份的公告", "ann_date": "20260615"}]},
    )
    assert ann["force_watch"] is True
    assert any("减持类公告" in r for r in ann["reasons"])


def test_scorecard_sentiment_breakdown_and_report() -> None:
    sc = build_stock_scorecard(
        {"history": {}, "quote": {}},
        {},
        {
            "sentiment_analysis": {"aggregate": {"score_100": 72}},
            "crowding_signal": {"crowding_risk": "high", "crowding_score": 5},
        },
    )
    sb = sc["sentiment_breakdown"]
    assert sb["news_tone"] == 72.0
    assert sb["crowding_risk"] == "high"
    assert sb["factor_score"] < 50

    md = render_daily_report(
        {
            "run_date": "2026-07-12",
            "data_quality": {"score": 0.9, "degraded": False, "missing": []},
            "intelligence": {
                "macro_raw": {
                    "sentiment_overview": {"aggregate": {"score_100": 55, "label": "neutral", "count": 10}},
                    "market_news_sentiment_scope": {
                        "ok": True,
                        "index": 0.82,
                        "label": "hot",
                        "percentile_1y": 85.0,
                        "latest_date": "2026-07-11",
                        "plain_note": "全市场新闻情绪温度计；仅作 A1 旁路，不进个股打分/不抬买入分",
                    },
                },
                "digest": {"executive_summary": "测试摘要"},
            },
            "market": {"analysis": {"phase": "range", "style": "balanced"}},
            "sectors": [],
            "stocks": [
                {
                    "code": "600519",
                    "analysis": {
                        "code": "600519",
                        "name": "贵州茅台",
                        "research_rating": "hold",
                        "factor_scorecard": sc,
                    },
                    "factor_scorecard": sc,
                }
            ],
            "recommendations": [
                {
                    "code": "600519",
                    "action": "watch",
                    "confidence": 0.5,
                    "rationale": "测试",
                    "factor_scorecard": sc,
                }
            ],
            "decision_summary": {},
        }
    )
    assert "数库新闻情绪指数" in md
    assert "拥挤风险" in md
    assert "新闻语调" in md
    assert "不抬分" in md
