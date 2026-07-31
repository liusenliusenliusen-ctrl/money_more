"""中长线三项：估值分位+股息、现金流闸、股债ERP。"""

from __future__ import annotations

from money_more.analysis.cashflow_quality import assess_ocf_quality
from money_more.analysis.decision_validator import validate_recommendations
from money_more.analysis.equity_bond import compute_erp
from money_more.analysis.factor_scorecard import build_stock_scorecard
from money_more.analysis.valuation import (
    blend_valuation_with_dividend,
    build_valuation_percentiles,
    dividend_score_from_yield,
)
from money_more.report.writer import render_conclusion_card


def test_dividend_in_percentiles_and_score() -> None:
    hist = [
        {"pe_ttm": float(i), "pb": float(i) / 10, "dv_ratio": 1.0 + (i % 5) * 0.2}
        for i in range(1, 101)
    ]
    pct = build_valuation_percentiles(hist, {"pe_ttm": 10.0, "pb": 1.0, "dv_ratio": 3.5})
    assert pct["ok"] is True
    assert pct["dv_ratio"] == 3.5
    assert dividend_score_from_yield(3.5) == 80.0
    blended, ev = blend_valuation_with_dividend(78.0, 3.5)
    assert blended > 78.0
    assert any("股息率" in x for x in ev)


def test_scorecard_blends_dividend_and_ocf() -> None:
    sc = build_stock_scorecard(
        {
            "history": {},
            "quote": {},
            "ocf_quality": {
                "signal": "strong",
                "ocf_to_profit_avg": 1.1,
            },
        },
        {"research_rating": "hold", "confidence": 0.5},
        {
            "tushare": {
                "valuation": {
                    "latest": {"pe_ttm": 12.0, "pb": 1.2, "dv_ratio": 3.2},
                    "percentiles": {
                        "ok": True,
                        "pe_percentile": 12.0,
                        "pb_percentile": 15.0,
                        "label": "cheap",
                        "dv_ratio": 3.2,
                    },
                },
                "financials": {"indicators": [{"roe": 18, "grossprofit_margin": 45}]},
            }
        },
    )
    assert sc["scores"]["valuation"] >= 75
    assert any("股息率" in e for e in sc["evidence"]["valuation"])
    assert sc["scores"]["quality"] >= 70
    assert any("现金流" in e for e in sc["evidence"]["quality"])


def test_ocf_quality_blocks_paper_profit() -> None:
    bundle = {
        "financials": {
            "cashflow": [
                {"n_cashflow_act": -1e8},
                {"n_cashflow_act": -2e8},
            ],
            "income": [
                {"n_income": 5e7},
                {"n_income": 6e7},
            ],
            "indicators": [],
        }
    }
    q = assess_ocf_quality(bundle, require_periods=2, block_on_negative_ocf=True)
    assert q["signal"] == "weak"
    assert q["block_buy"] is True
    assert q["ni_ocf_divergence"] is True


def test_ocf_from_fina_ratio() -> None:
    bundle = {
        "financials": {
            "indicators": [
                {"ocf_to_profit": 1.2, "roe": 15},
                {"ocf_to_profit": 0.9, "roe": 14},
            ],
            "cashflow": [],
            "income": [],
        }
    }
    q = assess_ocf_quality(bundle)
    assert q["signal"] == "strong"
    assert q["block_buy"] is False


def test_validator_blocks_buy_on_weak_ocf() -> None:
    recs, overs = validate_recommendations(
        [{"code": "600000", "action": "buy", "confidence": 0.9, "position_pct": 15}],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        quotes={"600000": 10.0},
        data_quality={"score": 0.9},
        ocf_quality={
            "600000": {
                "signal": "weak",
                "block_buy": True,
                "force_watch": True,
                "evidence": ["纸面富贵风险"],
            }
        },
    )
    assert recs[0]["action"] == "watch"
    assert any("现金流质量闸" in o for o in overs)


def test_erp_expensive_lowers_ceiling() -> None:
    # PE=20 → EY=5%；10Y=3.5% → ERP=150bp → expensive → 35%
    erp = compute_erp(cn_10y_pct=3.5, index_pe_ttm=20.0, max_total_cap=80.0)
    assert erp["ok"] is True
    assert erp["regime"] == "expensive"
    assert erp["implied_max_total_pct"] == 35.0

    # PE=12 → EY≈8.33%；10Y=2% → ERP≈633bp → attractive
    erp2 = compute_erp(cn_10y_pct=2.0, index_pe_ttm=12.0, max_total_cap=80.0)
    assert erp2["regime"] == "attractive"
    assert erp2["implied_max_total_pct"] == 80.0


def test_validator_erp_caps_total() -> None:
    # 两只各 20% → 总 40；ERP 上限 35 时应按比例压仓
    recs, overs = validate_recommendations(
        [
            {"code": "600000", "action": "buy", "confidence": 1.0, "position_pct": 20},
            {"code": "600036", "action": "buy", "confidence": 1.0, "position_pct": 20},
        ],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        quotes={"600000": 10.0, "600036": 30.0},
        data_quality={"score": 0.95},
        equity_bond={
            "ok": True,
            "regime": "expensive",
            "erp_bp": 150.0,
            "implied_max_total_pct": 35.0,
        },
    )
    assert any("equity_bond=" in o for o in overs)
    total = sum(float(r.get("position_pct") or 0) for r in recs)
    assert total <= 35.0 + 0.05


def test_a1_renders_erp_line() -> None:
    result = {
        "market": {
            "analysis": {
                "phase_label": "震荡",
                "style_label": "价值",
                "risk_level": "medium",
                "confidence": 0.6,
                "sector_allocation_hint": "均衡",
                "summary": "测试",
            }
        },
        "intelligence": {
            "macro_raw": {
                "global_liquidity": {
                    "stance": "mixed",
                    "us_10y": {"latest": 4.2},
                    "plain_note": "测试流动性",
                    "a_share_implication": "中性",
                }
            },
            "digest": {},
        },
        "equity_bond": {
            "ok": True,
            "index": "沪深300",
            "pe_ttm": 12.5,
            "earnings_yield_pct": 8.0,
            "erp_bp": 450.0,
            "implied_max_total_pct": 65.0,
            "regime": "neutral",
        },
        "recommendations": [],
        "decision_summary": {"holdings_basis": {"is_empty": True}},
        "data_quality": {"score": 0.9},
        "screen": {},
    }
    card = "\n".join(render_conclusion_card(result))
    assert "股债相对价值" in card
    assert "ERP=450" in card or "450" in card
