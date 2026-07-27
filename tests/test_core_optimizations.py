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


def test_empty_holdings_blocks_hold_sell_add():
    recs, overs = validate_recommendations(
        [
            {"code": "600519", "action": "hold", "confidence": 0.8, "position_pct": 10},
            {"code": "300750", "action": "sell", "confidence": 0.7, "position_pct": 0},
            {"code": "601318", "action": "add", "confidence": 0.8, "position_pct": 10},
        ],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        quotes={"600519": 1700.0, "300750": 200.0, "601318": 50.0},
        data_quality={"score": 0.9},
    )
    by = {r["code"]: r["action"] for r in recs}
    assert by["600519"] == "watch"
    assert by["300750"] == "watch"
    assert by["601318"] == "buy"
    assert any("空仓禁止" in o for o in overs)


def test_allowed_codes_whitelist():
    recs, overs = validate_recommendations(
        [{"code": "999999", "action": "buy", "confidence": 0.9, "position_pct": 10}],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        quotes={"999999": 10.0},
        data_quality={"score": 0.9},
        allowed_codes={"600519", "300750"},
    )
    assert recs[0]["action"] == "watch"
    assert any("深度池" in o for o in overs)


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


def test_valuation_percentiles():
    from money_more.analysis.valuation import (
        build_valuation_percentiles,
        percentile_rank,
        valuation_score_from_percentiles,
    )

    hist = [{"pe_ttm": float(i), "pb": float(i) / 10} for i in range(1, 101)]
    pct = build_valuation_percentiles(hist, {"pe_ttm": 10.0, "pb": 1.0})
    assert pct["ok"] is True
    assert pct["pe_percentile"] == 9.5
    assert pct["label"] == "cheap"
    assert valuation_score_from_percentiles(10.0, 10.0) == 90.0
    assert percentile_rank([1, 2, 3], 0) is None


def test_scorecard_uses_percentiles():
    sc = build_stock_scorecard(
        {"history": {}, "quote": {}},
        {"research_rating": "hold", "confidence": 0.5},
        {
            "tushare": {
                "valuation": {
                    "latest": {"pe_ttm": 12.0, "pb": 1.2},
                    "percentiles": {
                        "ok": True,
                        "pe_percentile": 12.0,
                        "pb_percentile": 15.0,
                        "label": "cheap",
                    },
                }
            }
        },
    )
    assert sc["scores"]["valuation"] >= 75
    assert any("历史分位" in e for e in sc["evidence"]["valuation"])


def test_synthetic_calendar_and_northbound_freshness():
    from money_more.data.intelligence import (
        _macro_records_from_df,
        _northbound_freshness,
        _synthetic_calendar_from_macro_hard,
    )

    macro_hard = {"pmi": [{"月份": "2026年06月份", "制造业": 49.5}]}
    events = _synthetic_calendar_from_macro_hard(macro_hard, date(2026, 7, 12))
    assert len(events) == 1
    assert events[0]["event"] == "中国制造业PMI"
    assert events[0]["日期"] == "2026-06"

    # AkShare 宏观序列降序：head 取最新，合成日历不得落到 2008
    import pandas as pd

    pmi_df = pd.DataFrame(
        [
            {"月份": "2026年06月份", "制造业-指数": 50.3},
            {"月份": "2026年05月份", "制造业-指数": 50.0},
            {"月份": "2008年01月份", "制造业-指数": 53.0},
        ]
    )
    records = _macro_records_from_df(pmi_df, 6)
    synth = _synthetic_calendar_from_macro_hard({"pmi": records}, date(2026, 7, 23))
    assert synth[0]["日期"] == "2026-06"
    assert "2008" not in synth[0]["日期"]

    fresh = _northbound_freshness([{"日期": "2026-07-10"}], date(2026, 7, 12))
    assert fresh["stale"] is False
    assert fresh.get("trading_staleness_days") == 0
    stale = _northbound_freshness([{"日期": "2026-07-01"}], date(2026, 7, 12))
    assert stale["stale"] is True
    # 周五数据 + 周一 as_of：仅 1 个交易日滞后，不应判 stale
    fri_pause = _northbound_freshness([{"日期": "2026-07-24"}], date(2026, 7, 27))
    assert fri_pause["stale"] is False


def test_parse_macro_period_date():
    from money_more.data.as_of import parse_macro_period_date, parse_record_date

    assert parse_macro_period_date({"月份": "2026年06月份"}) == date(2026, 6, 1)
    assert parse_macro_period_date({"月份": "2008年01月份"}) == date(2008, 1, 1)
    assert parse_record_date({"月份": "2026年06月份"}) == date(2026, 6, 1)


def test_macro_news_backfill_and_quality():
    from money_more.analysis.pipeline import DecisionPipeline
    from money_more.data.intelligence import _merge_macro_news_fallback

    macro = {
        "global_news": [{"title": "东财A", "content": "x"}],
        "global_news_sina": [{"title": "新浪B", "content": "y"}],
        "rss_important": [{"title": "财联社C", "content": "z"}],
        "rss_telegraph": [{"title": "东财A", "content": "dup"}],
    }
    merged = _merge_macro_news_fallback(macro, limit=10)
    assert len(merged) == 3
    titles = {x["title"] for x in merged}
    assert titles == {"东财A", "新浪B", "财联社C"}

    intel = {
        "policy_news": [{"title": "p"}],
        "global_news": [{"title": "g"}],
        "rss_telegraph": [{"title": "r"}],
        "tushare_macro_news": [{"title": "alt"}],
        "tushare_macro_backfill": True,
        "margin_trend": {"x": 1},
        "northbound_summary": [{"日期": "2026-07-10"}],
        "northbound_freshness": {"stale": False},
        "sentiment_overview": {"aggregate": {"score": 50}},
        "economic_calendar_synthetic": True,
        "sector_money_flow": {"top_inflow": [{"板块": "半导体", "净流入": 1.0e8}]},
        "macro_hard": {"pmi": []},
        "errors": ["Tushare 未配置", "tushare_macro_backfill_from_alt_sources"],
    }
    dq = DecisionPipeline._assess_data_quality(intel)
    assert dq["checks"]["tushare_macro"] is True
    assert "tushare_available" not in dq["missing"]
    assert dq["tushare_macro_backfill"] is True
    assert dq["score"] >= 0.7


def test_parse_record_date():
    from money_more.data.as_of import parse_record_date

    assert parse_record_date({"交易日": "2026-07-10"}) == date(2026, 7, 10)


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
    from money_more.analysis.context_builder import _tail_macro_hard, compact_macro_intel

    raw = {"policy_news": [{"a": i} for i in range(20)], "errors": [], "sentiment_overview": {"aggregate": {}}}
    c = compact_macro_intel(raw, max_news=6)
    assert len(c["policy_news"]) == 6

    hard = {
        "pmi": [
            {"月份": "2026年06月份", "制造业-指数": 50.3},
            {"月份": "2026年05月份", "制造业-指数": 50.0},
            {"月份": "2026年04月份", "制造业-指数": 49.8},
            {"月份": "2026年03月份", "制造业-指数": 49.5},
            {"月份": "2008年01月份", "制造业-指数": 53.0},
        ]
    }
    tailed = _tail_macro_hard(hard)
    assert len(tailed["pmi"]) == 3
    assert tailed["pmi"][0]["月份"] == "2026年06月份"
    assert all("2008" not in str(r.get("月份")) for r in tailed["pmi"])


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
    assert c.schedule.cadence == "tue_fri"
    assert c.schedule.run_hour == 1
    assert c.schedule.optimize_after_run is True
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


def test_schedule_gate_tue_fri(tmp_path: Path):
    from datetime import date

    from money_more.schedule_gate import should_run, write_last_run

    # 2026-07-28 = Tuesday, 2026-07-29 = Wednesday, 2026-07-31 = Friday
    tue = date(2026, 7, 28)
    wed = date(2026, 7, 29)
    fri = date(2026, 7, 31)
    ok, reason = should_run(tmp_path, cadence="tue_fri", today=tue)
    assert ok is True
    assert "周二/周五" in reason
    ok_w, reason_w = should_run(tmp_path, cadence="tue_fri", today=wed)
    assert ok_w is False
    assert "非周二/周五" in reason_w
    write_last_run(tmp_path, tue)
    ok_dup, _ = should_run(tmp_path, cadence="tue_fri", today=tue)
    assert ok_dup is False
    ok_f, _ = should_run(tmp_path, cadence="tue_fri", today=fri)
    assert ok_f is True
    ok_force, _ = should_run(tmp_path, cadence="tue_fri", today=wed, force=True)
    assert ok_force is True


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


def test_email_ready_and_preview():
    from money_more.config import EmailConfig
    from money_more.notify.emailer import _preview, email_ready

    ok, reason = email_ready(EmailConfig(enabled=False))
    assert ok is False
    assert "enabled" in reason
    ok2, _ = email_ready(
        EmailConfig(
            enabled=True,
            smtp_host="smtp.qq.com",
            smtp_user="a@qq.com",
            smtp_password="x",
            from_addr="a@qq.com",
            to_addrs=["a@qq.com"],
        )
    )
    assert ok2 is True
    assert "截断" in _preview("字" * 25000)


def test_optimize_prompt_includes_data_sources(tmp_path: Path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-07-12.md").write_text(
        "**数据质量**: 0.65 (OK)\n- 缺失项: economic_calendar\n**量化舆情分**: 48/100\n",
        encoding="utf-8",
    )
    from money_more.optimize.cursor_agent import build_optimize_prompt

    prompt = build_optimize_prompt("2026-07-12", project_root=tmp_path)
    assert "数据源" in prompt
    assert "舆情" in prompt
    assert "economic_calendar" in prompt
    assert "P0 数据源" in prompt


def test_multi_agent_orchestrator_fallback():
    from money_more.agents.orchestrator import AnalystAgent, MultiAgentOrchestrator, SynthesisAgent
    from money_more.llm.providers.base import LLMProvider

    class FakeProvider(LLMProvider):
        def __init__(self, name: str, payload: dict, fail: bool = False):
            self.name = name
            self.payload = payload
            self.fail = fail

        def complete_json(self, system_prompt, user_payload, **kwargs):
            if self.fail:
                raise RuntimeError("boom")
            out = dict(self.payload)
            return out

    primary = AnalystAgent(
        FakeProvider("deepseek", {"recommendations": [{"code": "600519"}], "portfolio_summary": "a"}),
        role="primary",
    )
    secondary = AnalystAgent(
        FakeProvider("cursor", {"recommendations": [{"code": "300750"}], "portfolio_summary": "b"}),
        role="secondary",
    )
    synth = SynthesisAgent(
        FakeProvider(
            "synth",
            {
                "recommendations": [{"code": "600519", "action": "hold"}],
                "portfolio_summary": "merged",
                "multi_agent": {"agreement": 0.5},
            },
        )
    )
    orch = MultiAgentOrchestrator(primary, secondary, synth, parallel=False)
    out = orch.analyze_json("sys", {"x": 1}, required_keys=["recommendations", "portfolio_summary"])
    assert out["portfolio_summary"] == "merged"
    assert out["_multi_agent"]["primary"] == "deepseek"

    # secondary fail → primary only
    orch2 = MultiAgentOrchestrator(
        primary,
        AnalystAgent(FakeProvider("cursor", {}, fail=True), role="secondary"),
        synth,
        parallel=False,
    )
    out2 = orch2.analyze_json("sys", {"x": 1}, required_keys=["recommendations", "portfolio_summary"])
    assert out2["_multi_agent_fallback"] == "primary_only"


def test_agents_config_defaults():
    from money_more.config import load_config

    c = load_config()
    assert c.agents.enabled is True
    assert c.agents.secondary_provider == "cursor"
    assert c.agents.synthesizer_provider == "deepseek"


def test_load_report_excerpt(tmp_path: Path):
    from money_more.analysis.pipeline import DecisionPipeline
    from money_more.config import AppConfig, PathsConfig

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-07-01.md").write_text(
        "# report\n**数据质量**: 0.8\n\n## 个股 600519\n贵州茅台建议买入，逻辑是估值低位。\n",
        encoding="utf-8",
    )
    cfg = AppConfig(paths=PathsConfig(reports=str(reports)), project_root=tmp_path)

    class _P:
        config = cfg

    excerpt = DecisionPipeline._load_report_excerpt(_P(), "2026-07-01", "600519")  # type: ignore[arg-type]
    assert excerpt["exists"] is True
    assert "600519" in excerpt.get("excerpt", "")


def test_historical_reports_corpus(tmp_path: Path):
    from datetime import date

    from money_more.analysis.review_history import load_historical_reports_corpus

    reports = tmp_path / "reports"
    dig = reports / "digests"
    dig.mkdir(parents=True)
    (reports / "2026-04-01.md").write_text(
        "**数据质量**: 0.7\n\n## 0. 情报\n四月市场偏弱，防御优先。\n",
        encoding="utf-8",
    )
    (reports / "2026-07-01.md").write_text(
        "**数据质量**: 0.8\n\n## 0. 情报\n七月科技主线升温。\n- 600519 buy\n",
        encoding="utf-8",
    )
    (dig / "2026-07-01.json").write_text(
        '{"run_date":"2026-07-01","market_phase":"range","recommendations":[{"code":"600519","action":"buy"}]}',
        encoding="utf-8",
    )
    corpus = load_historical_reports_corpus(
        reports, as_of=date(2026, 7, 12), lookback_days=120, max_reports=10
    )
    assert corpus["report_count"] >= 2
    assert corpus["digest_count"] >= 1
    assert any(r["date"] == "2026-04-01" for r in corpus["reports"])


def test_normalize_sector_summary_em_columns():
    import pandas as pd

    from money_more.data.fetcher import _normalize_sector_summary, build_sector_money_flow, sector_money_flow_present

    raw = pd.DataFrame(
        [
            {"名称": "半导体", "今日涨跌幅": "3.5", "今日主力净流入-净额": "1200000000"},
            {"名称": "银行", "今日涨跌幅": "-1.2", "今日主力净流入-净额": "-50000000"},
        ]
    )
    normalized = _normalize_sector_summary(
        raw,
        {"名称": "板块", "今日涨跌幅": "涨跌幅", "今日主力净流入-净额": "净流入"},
    )
    assert list(normalized["板块"]) == ["半导体", "银行"]
    flow = build_sector_money_flow(normalized, limit=2)
    assert flow["top_gainers"][0]["板块"] == "半导体"
    assert flow["top_losers"][-1]["板块"] == "银行"
    assert flow["top_inflow"][0]["净流入"] == 1200000000.0
    assert sector_money_flow_present(flow) is True
    assert sector_money_flow_present({"top_inflow": []}) is False


def test_fetch_sector_board_summary_fallback(monkeypatch):
    import pandas as pd

    from money_more.data.fetcher import fetch_sector_board_summary

    calls: list[str] = []

    def fail_ths_summary():
        calls.append("ths_summary")
        raise RuntimeError("ths down")

    def ok_em_rank(indicator: str = "今日", sector_type: str = "行业资金流"):
        calls.append("em_rank")
        return pd.DataFrame(
            [{"名称": "新能源", "今日涨跌幅": 2.1, "今日主力净流入-净额": 880000000}]
        )

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_board_industry_summary_ths", fail_ths_summary)
    def fail_ths_flow(symbol: str = "即时"):
        calls.append("ths_industry_flow")
        raise RuntimeError("ths flow down")

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_fund_flow_industry", fail_ths_flow)
    monkeypatch.setattr("money_more.data.fetcher.ak.stock_sector_fund_flow_rank", ok_em_rank)

    df, source, errors = fetch_sector_board_summary()
    assert source == "em_rank"
    assert df.iloc[0]["板块"] == "新能源"
    assert calls == ["ths_summary", "ths_industry_flow", "em_rank"]
    assert any("ths down" in e for e in errors)


def test_sector_money_flow_quality_gate():
    from money_more.analysis.pipeline import DecisionPipeline

    base = {
        "policy_news": [{"title": "p"}],
        "global_news": [{"title": "g"}],
        "rss_telegraph": [{"title": "r"}],
        "tushare_macro_news": [{"title": "alt"}],
        "margin_trend": {"x": 1},
        "northbound_summary": [{"日期": "2026-07-10"}],
        "northbound_freshness": {"stale": False},
        "sentiment_overview": {"aggregate": {"score": 50}},
        "economic_calendar_synthetic": True,
        "macro_hard": {"pmi": [{}]},
        "global_liquidity": {"stance": "mixed", "us_10y": {"latest": 4.5}},
        "errors": [],
    }
    missing = DecisionPipeline._assess_data_quality({**base, "sector_money_flow": {}})
    assert "sector_money_flow" in missing["missing"]
    ok = DecisionPipeline._assess_data_quality(
        {
            **base,
            "sector_money_flow": {"top_inflow": [{"板块": "白酒", "净流入": 2.87e9}]},
        }
    )
    assert ok["checks"]["sector_money_flow"] is True
    assert ok["score"] == 1.0


def test_render_conclusion_card_and_cross_links():
    from money_more.report.writer import render_conclusion_card, render_daily_report

    result = {
        "run_date": "2026-07-12",
        "data_quality": {"score": 0.9, "degraded": False, "note": "ok", "missing": []},
        "intelligence": {
            "digest": {
                "headline_themes": ["地缘风险", "半导体存储"],
                "risk_flags": ["中东冲突升级"],
                "executive_summary": "摘要",
            },
            "macro_raw": {"sentiment_overview": {"aggregate": {"score_100": 51, "label": "neutral", "count": 10}}},
        },
        "market": {
            "analysis": {
                "phase_label": "震荡偏强，结构性行情",
                "style_label": "题材驱动，科技成长为主",
                "risk_level": "medium",
                "confidence": 0.75,
                "primary_driver": "政策与产业主题",
                "sector_allocation_hint": "偏成长",
                "summary": "指数震荡结构分化",
                "contradictions": ["政策宽松与地缘风险拉锯"],
                "invalidation": ["地缘失控引发全面避险"],
                "vs_prior": {"continuity": "continuation", "what_changed": ["地缘升级"]},
            }
        },
        "sectors": [
            {
                "sector": "新能源",
                "analysis": {
                    "sector": "新能源",
                    "worth_research": True,
                    "priority": "high",
                    "policy_wind": "tailwind",
                    "prosperity": "up",
                    "valuation": "cheap",
                    "sentiment": {"crowding_risk": "low", "quant_score_100": 66},
                    "summary": "左侧窗口",
                    "narrative": "政策与基本面支撑",
                },
            },
            {
                "sector": "半导体",
                "analysis": {
                    "sector": "半导体",
                    "worth_research": True,
                    "priority": "high",
                    "policy_wind": "tailwind",
                    "prosperity": "up",
                    "valuation": "expensive",
                    "sentiment": {"crowding_risk": "high"},
                    "summary": "拥挤",
                },
            },
        ],
        "stocks": [
            {"code": "300750", "analysis": {"code": "300750", "name": "宁德时代", "research_rating": "buy"}},
            {"code": "600519", "analysis": {"code": "600519", "name": "贵州茅台", "research_rating": "buy"}},
        ],
        "recommendations": [
            {
                "code": "300750",
                "action": "watch",
                "confidence": 0.55,
                "position_pct": 0,
                "rationale": "placeholder",
                "sector_tag": "新能源",
                "invalidation": "半年报不及预期",
            },
            {
                "code": "600519",
                "action": "hold",
                "confidence": 0.45,
                "rationale": "持仓等待旺季",
                "sector_tag": "白酒",
            },
        ],
        "decision_summary": {
            "market_context": "震荡偏强，成长活跃但半导体拥挤不宜追高，新能源等资金确认",
            "portfolio_summary": "维持茅台，观察宁德",
        },
    }
    # 构造超长理由（旧版 _one_line(..., 64) 会截断；现应全文保留）
    long_why = (
        "基本面强但资金未稳，需等待北向与板块资金确认后再动手；"
        "同时关注美债收益率回落与微观结构修复，二者齐备前保持观察；"
        "若出现板块资金连续三日净流出或北向大幅撤离，则继续观望。"
    )
    assert len(long_why) > 64
    result["recommendations"][0]["rationale"] = long_why
    card = "\n".join(render_conclusion_card(result))
    assert "## 结论卡（速读）" in card
    assert "### A. 主结论" in card
    assert "#### A1. 分析：现在怎么看" in card
    assert "#### A2. 预测：接下来怎么预期" in card
    assert "#### A3. 动作：怎么做（④风控终局）" in card
    assert "### B. 推理链" in card
    assert "#### B1. 宏观 → 板块" in card
    assert "#### B2. 个股决策链" in card
    assert "### C. 侧栏" in card
    assert "【侧栏语气】" not in card
    assert "阅读顺序" in card
    assert "观察" in card and "300750" in card
    assert "新能源" in card and "等确认" in card
    assert "回避追高" in card  # 半导体 expensive+crowding
    assert long_why in card  # 动作理由全文
    assert "理由: " + long_why in card
    # A3 动作应在 B / C 之前
    assert card.index("#### A3. 动作") < card.index("### C. 侧栏")
    assert card.index("#### A3. 动作") < card.index("### B. 推理链")

    md = render_daily_report(result)
    assert "## 结论卡（速读）" in md
    assert "## 详细论证" in md
    assert "### A. 展开主结论" in md
    assert "### B. 展开推理链" in md
    assert "### C. 展开侧栏" in md
    assert "**落到动作**" in md
    assert "#### B2. 个股决策链" in md
    assert "###### ① 研究" in md
    assert "###### ④ 风控终局" in md
    assert "#### A3. 动作：怎么做（索引" in md
    assert "## D. 复盘与经验" not in md
    assert "## 附录：模拟账本" not in md
    assert "**板块**: 新能源" in md or "板块:新能源" in md
    assert "### B. 推理链" in md
    assert "#### B1. 宏观 → 板块" in md
    assert "①研究评级" in md or "研究评级" in md

    # 维度复盘渲染
    result["dimension_reviews"] = [
        {
            "dimension": "market",
            "subject": "震荡偏强",
            "outcome": "correct",
            "as_of_forecast": "2026-06-20",
            "diagnosis_category": "macro",
            "diagnosis": "阶段判断延续成立",
            "lesson": "结构市不宜赌指数方向",
        }
    ]
    result["history_patterns"] = ["左侧须等资金确认"]
    from money_more.report.writer import render_review_report

    md2 = render_review_report(result)
    assert "## 维度复盘" in md2
    assert "市场阶段" in md2 or "震荡偏强" in md2
    assert "🔁 [pattern]" in md2
    main2 = render_daily_report(result)
    assert "## 维度复盘" not in main2


def test_build_prior_dimension_forecasts(tmp_path: Path):
    from money_more.analysis.review_history import build_prior_dimension_forecasts

    dig = tmp_path / "digests"
    dig.mkdir()
    old = {
        "run_date": "2026-06-01",
        "market_phase": "range",
        "market_phase_label": "震荡",
        "market_style": "theme",
        "primary_driver": "政策",
        "sectors": [{"sector": "新能源", "priority": "high", "valuation": "cheap"}],
        "headline_themes": ["碳达峰"],
        "recommendations": [{"code": "300750", "action": "watch"}],
    }
    (dig / "2026-06-01.json").write_text(
        __import__("json").dumps(old, ensure_ascii=False), encoding="utf-8"
    )
    # too recent — should be excluded when min_age=14 and as_of=2026-07-12
    (dig / "2026-07-10.json").write_text(
        __import__("json").dumps({"run_date": "2026-07-10", "market_phase": "bull"}, ensure_ascii=False),
        encoding="utf-8",
    )
    items = build_prior_dimension_forecasts(
        tmp_path, as_of=date(2026, 7, 12), lookback_days=120, min_age_days=14, max_items=8
    )
    assert any(i.get("date") == "2026-06-01" for i in items)
    hit = next(i for i in items if i["date"] == "2026-06-01")
    assert hit["market"]["phase"] == "range"
    assert hit["sectors"][0]["sector"] == "新能源"
    assert hit.get("matured") is True
    # 较新材料可纳入窗口但标 matured=False
    young = [i for i in items if i.get("date") == "2026-07-10"]
    if young:
        assert young[0].get("matured") is False


def test_decision_digest_includes_dimensions():
    from money_more.analysis.decision_digest import build_decision_digest

    d = build_decision_digest(
        {
            "run_date": "2026-07-12",
            "market": {
                "analysis": {
                    "phase": "range",
                    "phase_label": "震荡偏强",
                    "style": "theme",
                    "primary_driver": "政策",
                    "invalidation": ["地缘失控"],
                }
            },
            "intelligence": {"digest": {"headline_themes": ["半导体"], "risk_flags": ["地缘"]}},
            "sectors": [
                {
                    "analysis": {
                        "sector": "新能源",
                        "priority": "high",
                        "prosperity": "up",
                        "valuation": "cheap",
                        "sentiment": {"crowding_risk": "low"},
                    }
                }
            ],
            "recommendations": [],
            "data_quality": {"score": 1.0},
        }
    )
    assert d["market_phase_label"] == "震荡偏强"
    assert d["sectors"][0]["sector"] == "新能源"
    assert d["headline_themes"] == ["半导体"]


def test_parse_and_merge_email_addrs():
    from money_more.config import EmailConfig, _merge_email_addrs, parse_email_addrs

    assert parse_email_addrs("a@qq.com, b@example.com") == ["a@qq.com", "b@example.com"]
    assert parse_email_addrs("a@qq.com;b@example.com") == ["a@qq.com", "b@example.com"]
    assert parse_email_addrs(["a@qq.com", "b@example.com, c@x.com"]) == [
        "a@qq.com",
        "b@example.com",
        "c@x.com",
    ]
    merged = _merge_email_addrs("a@qq.com, b@x.com", ["B@x.com", "c@y.com"])
    assert merged == ["a@qq.com", "b@x.com", "c@y.com"]
    # 默认不发优化邮件
    assert EmailConfig().send_optimize is False
    assert EmailConfig().send_analysis is True


def test_email_ledger_guide_once(tmp_path: Path):
    from money_more.notify.email_ledger import (
        has_received_guide,
        record_send,
        split_by_guide_status,
        load_ledger,
    )

    root = tmp_path
    first, returning = split_by_guide_status(root, ["A@qq.com", "b@x.com"])
    assert first == ["A@qq.com", "b@x.com"]
    assert returning == []

    record_send(
        root,
        to_addrs=["A@qq.com"],
        subject="test",
        ok=True,
        kind="analysis",
        guide_attached=True,
    )
    assert has_received_guide(root, "a@qq.com")
    assert not has_received_guide(root, "b@x.com")

    first2, returning2 = split_by_guide_status(root, ["a@qq.com", "b@x.com"])
    assert first2 == ["b@x.com"]
    assert returning2 == ["a@qq.com"]

    ledger = load_ledger(root)
    assert ledger["recipients"]["a@qq.com"]["send_count"] == 1
    assert ledger["recipients"]["a@qq.com"]["guide_sent_at"]
    assert len(ledger["sends"]) == 1


def test_extract_policy_news_from_pool_filters_keywords() -> None:
    from datetime import date

    from money_more.data.intelligence import _extract_policy_news_from_pool

    pool = [
        {"title": "马斯克谈AI", "日期": "2026-07-25"},
        {"title": "证监会召开座谈会稳市", "日期": "2026-07-25"},
        {"title": "国新500亿回购增持", "日期": "2026-07-24"},
    ]
    out = _extract_policy_news_from_pool(
        pool, as_of=date(2026, 7, 26), lookback_days=14, limit=5
    )
    titles = {i["title"] for i in out}
    assert "证监会召开座谈会稳市" in titles
    assert "国新500亿回购增持" in titles
    assert "马斯克谈AI" not in titles
