"""Unit tests for as_of, validator, scorecard."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from money_more.analysis.decision_validator import enrich_holdings, validate_recommendations
from money_more.analysis.factor_scorecard import build_stock_scorecard
from money_more.data.as_of import filter_calendar_upcoming, filter_records_by_date, parse_as_of, ymd
from money_more.storage.db import Database


def test_parse_as_of():
    assert parse_as_of("2026-07-09") == date(2026, 7, 9)
    assert ymd(date(2026, 7, 9), -1) == "20260708"


def test_filter_stale_news():
    as_of = date(2026, 7, 9)
    records = [
        {"日期": "2024-04-24", "title": "old"},
        {"日期": "2026-07-08", "title": "fresh"},
        {"title": "no_date"},
    ]
    kept = filter_records_by_date(records, as_of, lookback_days=7)
    titles = {r.get("title") for r in kept}
    assert "fresh" in titles
    assert "old" not in titles
    assert "no_date" in titles


def test_calendar_window():
    as_of = date(2026, 7, 9)
    records = [
        {"日期": "2025-11-01", "event": "old"},
        {"日期": "2026-07-15", "event": "soon"},
    ]
    kept = filter_calendar_upcoming(records, as_of, ahead_days=21)
    assert any(r.get("event") == "soon" for r in kept)
    assert not any(r.get("event") == "old" for r in kept)


def test_validate_clamps_and_holding_buy_to_add():
    recs, overs = validate_recommendations(
        [
            {
                "code": "600519",
                "action": "buy",
                "confidence": 1.5,
                "position_pct": 40,
                "stop_loss": 1000,
                "target_price": 2000,
            }
        ],
        holdings=[{"code": "600519", "quantity": 100, "cost": 1680}],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 8,
            "take_profit_pct": 25,
        },
        quotes={"600519": 1700.0},
        data_quality={"score": 0.9},
    )
    assert recs[0]["action"] == "add"
    assert recs[0]["confidence"] == 1.0
    assert recs[0]["position_pct"] <= 20
    assert recs[0]["stop_loss"] >= 1680 * 0.92 * 0.98
    assert overs


def test_degraded_forbids_new_buys():
    recs, overs = validate_recommendations(
        [{"code": "300750", "action": "buy", "confidence": 0.9, "position_pct": 15}],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 8,
            "take_profit_pct": 25,
        },
        quotes={"300750": 200.0},
        data_quality={"score": 0.3},
    )
    assert recs[0]["action"] == "watch"
    assert any("禁止新买" in o or "0.4" in o for o in overs)


def test_enrich_holdings():
    class H:
        code = "600519"
        quantity = 100
        cost = 1680.0

    rows = enrich_holdings([H()], {"600519": 1700.0})
    assert rows[0]["unrealized_pnl_pct"] == round((1700 - 1680) / 1680 * 100, 2)
    assert rows[0]["weight_pct"] == 100.0


def test_scorecard_bullish_bias():
    sc = build_stock_scorecard(
        {
            "history": {
                "above_ma20": True,
                "change_pct": 2.0,
                "close": 100,
                "ma5": 102,
                "ma20": 98,
                "high_20d": 105,
                "low_20d": 90,
            },
            "quote": {},
            "fund_flow": {"net_5d": 20000},
        },
        {"research_rating": "buy", "confidence": 0.8},
        {"sentiment_analysis": {"aggregate": {"score_100": 70}}},
    )
    assert sc["total_score"] >= 55
    assert sc["signal"] in ("bullish", "constructive", "neutral")


def test_paper_trade_stats(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    tid = db.open_paper_trade(
        recommendation_id=1,
        stock_code="600519",
        action="buy",
        entry_date="2026-07-01",
        entry_price=100.0,
        stop_loss=92.0,
        target_price=125.0,
        position_pct=10,
    )
    db.update_paper_trade(
        trade_id=tid,
        current_price=110.0,
        return_pct=10.0,
        status="closed",
        exit_date="2026-07-08",
        exit_price=110.0,
        exit_reason="take_profit",
        max_dd_pct=-2.0,
    )
    stats = db.get_paper_trade_stats()
    assert stats["closed"] == 1
    assert stats["hit_rate"] == 1.0
    assert stats["avg_return_pct"] == 10.0


def test_lesson_dedup(tmp_path: Path):
    db = Database(tmp_path / "t2.db")
    assert db.insert_lesson_if_new("meta", "same lesson") is True
    assert db.insert_lesson_if_new("meta", "same lesson") is False


def test_cross_check_mismatch():
    from money_more.analysis.cross_check import apply_hard_gates, cross_check_stock

    x = cross_check_stock(
        {"history": {"close": 100.0}, "quote": {}},
        {"valuation": {"latest": {"close": 103.0, "pe_ttm": 20, "pb": 2}}},
    )
    assert x["ok"] is False
    assert x["confidence_haircut"] > 0

    g = apply_hard_gates(
        "600000",
        {"quote": {"名称": "ST示例", "涨跌幅": 0.1}, "history": {"change_pct": 0.1, "volume": 1}},
        {},
    )
    assert g["block_buy"] is True


def test_hard_gate_in_validator():
    recs, overs = validate_recommendations(
        [{"code": "600000", "action": "buy", "confidence": 0.9, "position_pct": 10}],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 8,
            "take_profit_pct": 25,
        },
        quotes={"600000": 10.0},
        data_quality={"score": 0.9},
        hard_gates={"600000": {"block_buy": True, "force_watch": True, "reasons": ["ST"]}},
    )
    assert recs[0]["action"] == "watch"
    assert any("硬门禁" in o for o in overs)


def test_invalidation_ma20():
    from money_more.analysis.invalidation import evaluate_invalidation

    r = evaluate_invalidation("收盘跌破MA20", {"history": {"close": 90, "ma20": 100, "above_ma20": False}})
    assert r["invalidated"] is True
    r2 = evaluate_invalidation("收盘跌破MA20", {"history": {"close": 110, "ma20": 100, "above_ma20": True}})
    assert r2["invalidated"] is False


def test_open_questions_expiry():
    from money_more.analysis.trend import TrendReportBuilder

    qs = TrendReportBuilder._merge_open_questions(
        [{"text": "旧问题", "opened_on": "2026-01-01", "last_confirmed": "2026-01-01", "status": "open"}],
        {
            "intelligence": {"digest": {"risk_flags": ["新风险A"]}},
            "recommendations": [],
        },
        "2026-07-09",
    )
    texts = {q["text"]: q["status"] for q in qs}
    assert texts.get("旧问题") == "stale"
    assert texts.get("新风险A") == "open"


def test_compact_macro():
    from money_more.analysis.context_builder import compact_macro_intel

    raw = {"policy_news": [{"a": i} for i in range(20)], "errors": [], "sentiment_overview": {"aggregate": {}}}
    c = compact_macro_intel(raw, max_news=6)
    assert len(c["policy_news"]) == 6


def test_ashare_costs():
    from money_more.analysis.costs import apply_ashare_costs

    assert apply_ashare_costs(10.0) < 10.0


def test_pearson_ic():
    from money_more.analysis.factor_ic import pearson

    assert pearson([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert pearson([1, 2], [1, 2]) is None


def test_weights_from_ic():
    from money_more.analysis.weight_adapt import weights_from_ic

    w = weights_from_ic(
        {
            "ok": True,
            "ics": {
                "momentum": {"ic": -0.2, "n": 20},
                "valuation": {"ic": 0.2, "n": 20},
            },
        }
    )
    assert w["momentum"] < w["valuation"]


def test_sector_infer():
    from money_more.analysis.sector_map import infer_sector

    assert infer_sector("600519") == "白酒"


def test_risk_check_book():
    from money_more.analysis.risk_check import risk_check_book

    r = risk_check_book(
        [
            {"code": "600519", "action": "buy", "position_pct": 15, "sector_tag": "白酒"},
            {"code": "000858", "action": "buy", "position_pct": 15, "sector_tag": "白酒"},
        ],
        max_single=20,
        max_total=80,
        max_sector=20,
    )
    assert r["ok"] is False
    assert any("白酒" in x for x in r["issues"])


def test_midlong_defaults():
    from money_more.config import load_config

    c = load_config()
    assert c.schedule.cadence == "every_5_days"
    assert c.schedule.interval_days == 5
    assert c.schedule.run_hour == 1
    assert c.analysis.investment_horizon == "medium_long"
    assert c.analysis.review_min_hold_days >= 14
    assert c.trading.stop_loss_pct >= 12


def test_schedule_gate(tmp_path: Path):
    from datetime import date, timedelta

    from money_more.schedule_gate import should_run, write_last_run

    today = date(2026, 7, 11)
    ok, _ = should_run(tmp_path, interval_days=5, today=today)
    assert ok is True
    write_last_run(tmp_path, today)
    ok2, reason = should_run(tmp_path, interval_days=5, today=today + timedelta(days=3))
    assert ok2 is False
    assert "下次应跑" in reason
    ok3, _ = should_run(tmp_path, interval_days=5, today=today + timedelta(days=5))
    assert ok3 is True
    ok4, _ = should_run(tmp_path, interval_days=5, today=today + timedelta(days=1), force=True)
    assert ok4 is True


def test_optimize_pause_lock(tmp_path: Path):
    from money_more.optimize.workspace_guard import should_skip_optimize

    skip, reason = should_skip_optimize(tmp_path, skip_if_dirty=False, respect_human_lock=True)
    assert skip is False
    lock = tmp_path / "logs" / "OPTIMIZE_PAUSE"
    lock.parent.mkdir(parents=True)
    lock.write_text("cli editing\n", encoding="utf-8")
    skip2, reason2 = should_skip_optimize(tmp_path, skip_if_dirty=False, respect_human_lock=True)
    assert skip2 is True
    assert "OPTIMIZE_PAUSE" in reason2
