"""数据源台账与报告文首渲染。"""

from __future__ import annotations

from money_more.analysis.data_sources_ledger import (
    build_data_sources_ledger,
    render_data_sources_section,
)
from money_more.report.writer import render_daily_report


def test_ledger_marks_sina_spot_fallback():
    result = {
        "data_quality": {
            "score": 1.0,
            "degraded": False,
            "note": "数据完整度尚可",
            "missing": [],
            "checks": {"stock_screen": True, "screen_coverage": True},
        },
        "screen": {
            "enabled": True,
            "ok": True,
            "degraded": False,
            "spot_source": "sina",
            "universe_size_raw": 5527,
            "universe_size": 400,
            "quant_size": 50,
            "deep_size": 15,
            "plain_note": "备源测试",
        },
        "intelligence": {
            "macro_raw": {
                "policy_news": [{"title": "x"}],
                "global_news": [{"title": "y"}],
                "rss_telegraph": [{"title": "z"}],
                "margin_trend": [{"v": 1}],
                "northbound_summary": {"net": 1},
                "northbound_freshness": {"stale": False, "latest_date": "2026-07-17", "staleness_days": 2},
                "sector_money_flow": {
                    "top_gainers": [{"name": "银行"}],
                    "top_inflow": [{"name": "银行"}],
                },
                "sector_money_flow_source": "ths_summary",
                "sentiment_overview": {"aggregate": {"score_100": 54, "label": "neutral"}},
                "economic_calendar": [{"e": 1}],
                "macro_hard": {"pmi": 50},
                "global_liquidity": {"stance": "neutral", "source": ["bond_zh_us_rate"]},
                "tushare_macro_news": [{"title": "t"}],
                "tushare_macro_backfill": True,
                "errors": [
                    "major_news: 抱歉，您没有接口(major_news)访问权限",
                    "人气榜: push2.eastmoney.com",
                ],
            }
        },
        "stocks": [{"code": "600519", "analysis": {"code": "600519"}}],
    }
    ledger = build_data_sources_ledger(result)
    by_name = {r["name"]: r for r in ledger["rows"]}
    assert by_name["全 A 现货快照"]["status"] == "fallback"
    assert by_name["全 A 现货快照"]["obtained"] is True
    assert "sina" in by_name["全 A 现货快照"]["provider"]
    assert by_name["行业/板块资金流"]["status"] == "ok"
    assert by_name["Tushare 宏观/公司增强"]["status"] == "fallback"
    assert by_name["舆情/情绪量化"]["status"] == "degraded"

    macro_fb = {
        **result["intelligence"]["macro_raw"],
        "hot_rank_source": "xueqiu_follow",
        "errors": result["intelligence"]["macro_raw"]["errors"] + ["hot_rank_fallback:xueqiu_follow"],
    }
    result_fb = {**result, "intelligence": {"macro_raw": macro_fb}}
    ledger_fb = build_data_sources_ledger(result_fb)
    by_fb = {r["name"]: r for r in ledger_fb["rows"]}
    assert by_fb["舆情/情绪量化"]["status"] == "ok"
    assert "雪球" in by_fb["舆情/情绪量化"]["provider"]

    md_lines = render_data_sources_section(result)
    md = "\n".join(md_lines)
    assert "## 数据源说明（本轮）" in md
    assert "全 A 现货快照" in md
    assert "后面怎么用" in md or "应用" in md or "怎么用" in md


def test_report_starts_with_data_sources_section():
    result = {
        "run_date": "2026-07-19",
        "investment_horizon": "medium_long",
        "data_quality": {"score": 0.9, "degraded": False, "note": "ok", "missing": []},
        "screen": {
            "enabled": True,
            "ok": False,
            "degraded": True,
            "universe_size_raw": 0,
            "errors": ["spot_empty"],
            "plain_note": "行情接口失败",
        },
        "intelligence": {"macro_raw": {"errors": ["spot_empty"]}, "digest": {}},
        "market": {"analysis": {"phase_label": "震荡", "style_label": "价值", "risk_level": "medium"}},
        "sectors": [],
        "stocks": [],
        "recommendations": [],
        "decision_summary": {"holdings_basis": {"is_empty": True}},
    }
    md = render_daily_report(result)
    assert md.index("## 数据源说明（本轮）") < md.index("## 结论卡（速读）")
    assert "全 A 现货快照" in md
    assert "❌" in md


def test_ledger_policy_rss_extract_not_degraded() -> None:
    result = {
        "data_quality": {"score": 1.0, "degraded": False},
        "screen": {"enabled": False},
        "intelligence": {
            "macro_raw": {
                "policy_news": [{"title": "证监会稳市"}],
                "policy_news_source": "rss_global_extract",
                "global_news": [{"title": "g"}],
                "margin_trend": {"v": 1},
                "northbound_summary": [{"v": 1}],
                "northbound_freshness": {"stale": False, "latest_date": "2026-07-24"},
                "sector_money_flow": {"top_inflow": []},
                "sentiment_overview": {"aggregate": {"score_100": 50, "label": "neutral"}},
            }
        },
        "stocks": [],
    }
    ledger = build_data_sources_ledger(result)
    row = next(r for r in ledger["rows"] if r["name"] == "政策/联播类新闻")
    assert row["status"] == "ok"
    assert "快讯/RSS" in row["provider"]
