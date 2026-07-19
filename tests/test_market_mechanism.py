"""微观结构 + 信息完备性（无网络）。"""

from __future__ import annotations

import pandas as pd

from money_more.analysis.decision_validator import validate_recommendations
from money_more.analysis.info_completeness import assess_info_completeness
from money_more.analysis.market_microstructure import assess_market_microstructure


def test_microstructure_detects_sync_selloff() -> None:
    spot = pd.DataFrame(
        {
            "涨跌幅": [-2.0] * 80 + [0.5] * 20,
            "成交额": [1e8] * 100,
        }
    )
    overview = {"indices": [{"name": "上证指数", "change_pct": -2.8}], "limit_down_count": 55}
    out = assess_market_microstructure(overview, spot)
    assert out["regime"] in ("crowded_sync", "liquidity_stress", "elevated")
    assert out["fundamental_channel_ok"] is False or out["regime"] == "elevated"
    assert out["flags"]


def test_info_gap_on_big_move_without_news() -> None:
    snap = {
        "history": {"change_pct": 9.5, "volume": 1e6, "atr_pct_20d": 2.0},
        "quote": {},
        "news": [],
        "intelligence": {},
    }
    info = assess_info_completeness("600000", snap, {}, {"ok": True}, {})
    assert info["status"] == "gap_suspected"
    assert info["confidence_haircut"] > 0
    assert info["action_hint"] == "watch"
    assert "内幕" not in info["note"] and "操纵" not in info["note"]


def test_validator_blocks_buy_on_info_gap() -> None:
    recs, overs = validate_recommendations(
        [{"code": "600000", "action": "buy", "confidence": 0.8, "position_pct": 10}],
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        quotes={"600000": 10.0},
        data_quality={"score": 0.9},
        info_completeness={
            "600000": {
                "status": "gap_suspected",
                "severity": "high",
                "action_hint": "watch",
                "unexplained": ["价格异动且公开信息稀薄"],
                "confidence_haircut": 0.12,
            }
        },
    )
    assert recs[0]["action"] == "watch"
    assert any("信息缺口" in o for o in overs)


def test_validator_tightens_on_liquidity_stress() -> None:
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
        microstructure={"regime": "liquidity_stress"},
    )
    assert recs[0]["action"] == "watch"
    assert any("liquidity_stress" in o for o in overs)
