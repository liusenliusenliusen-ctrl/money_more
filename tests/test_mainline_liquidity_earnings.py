"""全球流动性分类 + 盈利预期修正（无网络）。"""

from __future__ import annotations

from money_more.analysis.decision_validator import validate_recommendations
from money_more.analysis.earnings_revision import assess_earnings_revision
from money_more.data.global_liquidity import _classify_stance


def test_classify_liquidity_tightening() -> None:
    stance = _classify_stance(
        {
            "us_10y": {"change_20d_bp": 40, "change_60d_bp": 50},
            "usd_cny": {"change_20d_pct": 1.2},
        }
    )
    assert stance == "tightening"


def test_classify_liquidity_easing() -> None:
    stance = _classify_stance(
        {
            "us_10y": {"change_20d_bp": -40, "change_60d_bp": -30},
            "usd_cny": {"change_20d_pct": -1.5},
        }
    )
    assert stance == "easing"


def test_earnings_forecast_downgrade() -> None:
    rev = assess_earnings_revision(
        {
            "forecast": [{"type": "预减", "summary": "净利润预计大幅下降", "p_change_min": -40, "p_change_max": -20}],
            "financials": {"indicators": []},
        }
    )
    assert rev["signal"] == "negative"
    assert rev["revision_bias"] == "downgrade"


def test_earnings_fina_upgrade() -> None:
    rev = assess_earnings_revision(
        {
            "forecast": [],
            "financials": {
                "indicators": [
                    {"netprofit_yoy": 35, "roe": 15},
                    {"netprofit_yoy": 10, "roe": 12},
                ]
            },
        }
    )
    assert rev["signal"] == "positive"
    assert rev["revision_bias"] == "upgrade"


def test_validator_blocks_buy_on_earnings_downgrade() -> None:
    recs, overs = validate_recommendations(
        [{"code": "600000", "action": "buy", "confidence": 0.85, "position_pct": 12}],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        quotes={"600000": 10.0},
        data_quality={"score": 0.9},
        earnings_revisions={
            "600000": {"signal": "negative", "evidence": ["预告预减"]},
        },
    )
    assert recs[0]["action"] == "watch"
    assert any("盈利预期下修" in o for o in overs)


def test_validator_tightens_on_global_tightening() -> None:
    recs, overs = validate_recommendations(
        [{"code": "600000", "action": "buy", "confidence": 0.9, "position_pct": 20}],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        quotes={"600000": 10.0},
        data_quality={"score": 0.9},
        global_liquidity={"stance": "tightening"},
    )
    assert any("global_liquidity=tightening" in o for o in overs)
    # 仓位应被总仓/单票约束后更紧（至少有 override）
    assert recs[0]["position_pct"] <= 20
