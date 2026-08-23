"""MM 三包优化：Wave1 短线清理 / Wave2 数据诚实 / Wave3 反脆弱闭环。"""

from __future__ import annotations

from money_more.analysis.decision_validator import validate_recommendations
from money_more.analysis.framework_gates import (
    build_contradiction_branches,
    build_framework_gate_state,
    evaluate_prior_contradiction_branches,
    sector_money_flow_ok,
)
from money_more.analysis.invalidation import evaluate_invalidation
from money_more.analysis.pipeline import DecisionPipeline, _social_financing_lag_months
from money_more.analysis.screen import _pe_to_score, _score_universe
from money_more.analysis.verify_tracker import build_verify_priors
from money_more.config import FrameworkGateConfig

import pandas as pd


def test_w1_paper_no_price_stop_close() -> None:
    """价触及原 stop 但 invalidation 未触发 → 不因价自动结案（逻辑在 _mark_paper_trades）。"""
    # 直接断言结案原因集合不含 stop/target
    import inspect

    src = inspect.getsource(DecisionPipeline._mark_paper_trades)
    assert "final_sell" in src
    assert "invalidation_fired" in src
    assert "horizon_" in src
    assert "stop_loss" not in src or "不用价格止损" in src
    assert "target_price" not in src or "不用价格止损" in src


def test_w1_invalidation_ma5_unchecked() -> None:
    r = evaluate_invalidation(
        "跌破MA5",
        {"history": {"close": 90, "ma20": 100, "above_ma20": False}},
    )
    assert r["invalidated"] is False
    assert r["fired"] == []
    assert any("MA5" in x or "ma5" in x.lower() for x in r["unchecked"])

    r2 = evaluate_invalidation(
        "单日跌幅超5%",
        {"history": {"close": 90, "ma20": 100}},
    )
    assert r2["invalidated"] is False
    assert r2["fired"] == []


def test_w1_invalidation_fundamental_still_fires_ma20() -> None:
    r = evaluate_invalidation(
        "收盘跌破MA20",
        {"history": {"close": 90, "ma20": 100, "above_ma20": False}},
    )
    assert r["invalidated"] is True
    assert r["fired"]


def test_w1_auto_sector_rejects_1d_window() -> None:
    macro = {
        "sector_money_flow": {
            "top_inflow": [{"板块": "半导体", "净流入": 1e9}],
            "top_gainers": [{"板块": "半导体"}],
        },
        "sector_money_flow_window": "1d",
        "sector_money_flow_source": "em_rank_1d",
    }
    meta = DecisionPipeline._auto_sectors_from_flow(macro, ["银行"], limit=3)
    assert meta["all"] == []
    assert meta["observe"] == []


def test_w1_auto_sector_accepts_5d() -> None:
    macro = {
        "sector_money_flow": {
            "top_inflow": [{"板块": "半导体", "净流入": 1e9}],
            "top_gainers": [{"板块": "半导体"}],
        },
        "sector_money_flow_window": "5d",
        "sector_money_flow_source": "em_rank_5d",
    }
    meta = DecisionPipeline._auto_sectors_from_flow(macro, ["银行"], limit=3)
    assert "半导体" in meta["all"]


def test_w1_validator_defaults_15_40() -> None:
    recs, overrides = validate_recommendations(
        [
            {
                "code": "600519",
                "action": "buy",
                "position_pct": 10,
                "confidence": 0.7,
                "stop_loss": 100.0,  # 过宽，应夹到 15%
            }
        ],
        holdings=[],
        constraints={"max_single_position_pct": 20, "max_total_position_pct": 80},
        quotes={"600519": 1680.0},
        allowed_codes={"600519"},
    )
    assert recs[0]["stop_loss"] >= 1680 * 0.85 * 0.98
    assert any("失效价带" in o for o in overrides)


def test_w2_missing_pe_scores_40() -> None:
    assert _pe_to_score(None) == 40.0
    df = pd.DataFrame(
        [
            {"code": "000001", "name": "有估值", "pe": 8.0, "pb": 1.0, "amount": 1e9},
            {"code": "000002", "name": "无估值", "pe": None, "pb": None, "amount": 1e9},
        ]
    )
    scored = _score_universe(df, priority_sectors=[], sector_boost=0.0)
    row_ok = scored[scored["code"] == "000001"].iloc[0]
    row_miss = scored[scored["code"] == "000002"].iloc[0]
    assert float(row_miss["screen_score"]) < float(row_ok["screen_score"])


def test_w2_sector_flow_requires_signed_inflow() -> None:
    assert sector_money_flow_ok({"top_inflow": [{"板块": "半导体"}]}) is False
    assert (
        sector_money_flow_ok(
            {
                "top_inflow": [
                    {"板块": "A", "净流入": 1e8},
                    {"板块": "B", "净流入": 2e8},
                ]
            }
        )
        is True
    )


def test_w2_social_financing_lag() -> None:
    from datetime import date

    hard = {"social_financing": [{"月份": "2026年04月份"}]}
    lag = _social_financing_lag_months(hard, as_of=date(2026, 7, 25))
    assert lag == 3
    dq = DecisionPipeline._assess_data_quality(
        {
            "as_of": "2026-07-25",
            "macro_hard": hard,
            "errors": [],
            "global_liquidity": {"stance": "neutral"},
            "sector_money_flow": {
                "top_inflow": [{"板块": "银行", "净流入": 1}],
                "rank_by_inflow": [{"板块": "银行", "净流入": 1}],
            },
            "policy_news": [{"title": "x"}],
            "global_news": [{"title": "y"}],
            "rss_telegraph": [{"title": "z"}],
            "margin_trend": {"ok": True},
            "northbound_summary": {"net": 1},
            "northbound_freshness": {"stale": False},
            "sentiment_overview": {"aggregate": 50},
            "economic_calendar": [{"e": 1}],
            "macro_hard_echo": {"pmi": 1},
            "tushare_macro_news": [{"t": 1}],
        }
    )
    assert dq.get("social_financing_lag_months") == 3
    assert "social_financing_fresh" in (dq.get("missing") or [])
    assert "勿称最新社融" in str(dq.get("note") or "")


def test_w2_macro_conflict_haircut() -> None:
    recs, overrides = validate_recommendations(
        [{"code": "600000", "action": "buy", "position_pct": 10, "confidence": 0.8}],
        holdings=[],
        constraints={"max_single_position_pct": 20, "max_total_position_pct": 80},
        allowed_codes={"600000"},
        macro_hard_meta={"pmi": {"agreement": "conflict"}},
        margin_trend={"agreement": "conflict"},
    )
    assert any("conflict" in o for o in overrides)
    assert recs[0]["validation"]["regime_mult"] < 1.0


def test_w3_branch_ids_and_prior_release() -> None:
    branches = build_contradiction_branches(True, ["PMI收缩(49)", "融资余额近窗收缩(-1%)"], [])
    ids = {b["branch_id"] for b in branches}
    assert "pmi_contraction" in ids
    assert "margin_shrink" in ids

    prior = evaluate_prior_contradiction_branches(
        branches,
        {
            "macro_hard": {"pmi": [{"制造业PMI": 51.0}]},
            "margin_trend": {"financing_balance_change_5d_pct": 0.5},
        },
    )
    assert all(b.get("status") == "improved" for b in prior)

    # 上轮未改善 → 仍激活
    fw = build_framework_gate_state(
        config=FrameworkGateConfig(),
        market_analysis={},
        macro_intel={},
        microstructure={},
        prior_context={
            "contradiction_branches": [
                {"branch_id": "pmi_contraction", "topic": "景气", "fact": "PMI收缩"}
            ]
        },
    )
    assert fw["contradiction_active"] is True
    assert "pmi_contraction" in fw["unresolved_prior_branches"]

    # 改善后释放
    fw2 = build_framework_gate_state(
        config=FrameworkGateConfig(),
        market_analysis={},
        macro_intel={"macro_hard": {"pmi": [{"制造业PMI": 51.2}]}},
        microstructure={},
        prior_context={
            "contradiction_branches": [
                {"branch_id": "pmi_contraction", "topic": "景气", "fact": "PMI收缩"}
            ]
        },
    )
    assert fw2["contradiction_active"] is False
    assert fw2["unresolved_prior_branches"] == []


def test_w3_verify_priors_and_cash_floor_theme_cap() -> None:
    priors = build_verify_priors(
        [
            {"action": "buy", "verdict": "miss", "sector": "半导体", "run_date": "2026-01-01"},
            {"action": "buy", "verdict": "miss", "sector": "半导体", "run_date": "2026-01-08"},
            {"action": "buy", "verdict": "miss", "sector": "半导体", "run_date": "2026-01-15"},
        ]
    )
    assert "半导体" in priors["forbid_sectors"]
    assert priors["confidence_mult"] == 0.75

    recs, overrides = validate_recommendations(
        [
            {
                "code": "600000",
                "action": "buy",
                "position_pct": 15,
                "confidence": 0.9,
                "sector_tag": "银行",
            },
            {
                "code": "600001",
                "action": "buy",
                "position_pct": 15,
                "confidence": 0.8,
                "sector_tag": "银行",
            },
            {
                "code": "600002",
                "action": "buy",
                "position_pct": 15,
                "confidence": 0.5,
                "sector_tag": "银行",
            },
        ],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 90,
            "max_deep_per_theme": 2,
        },
        allowed_codes={"600000", "600001", "600002"},
        equity_bond={
            "ok": True,
            "regime": "expensive",
            "erp_bp": 50,
            "implied_max_total_pct": 40,
            "implied_min_cash_pct": 60,
        },
        verify_ledger={"priors": priors},
    )
    assert any("现金地板" in o or "总仓上限" in o for o in overrides)
    # 主题帽：第三只应被压 watch
    actions = {r["code"]: r["action"] for r in recs}
    assert actions.get("600002") == "watch"
    assert any("主题" in o and "超限" in o for o in overrides)
