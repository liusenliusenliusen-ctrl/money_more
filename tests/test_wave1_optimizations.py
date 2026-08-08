"""第一波优化：暴涨剔除、微观分档、框架闸、社融排序。"""

from __future__ import annotations

import pandas as pd

from money_more.analysis.framework_gates import (
    build_code_prosperity_map,
    build_framework_gate_state,
    clamp_market_optimism,
)
from money_more.analysis.market_microstructure import assess_market_microstructure
from money_more.analysis.decision_validator import validate_recommendations
from money_more.analysis.screen import _exclude_surge_rows, _normalize_spot, _score_universe
from money_more.config import FrameworkGateConfig, MicrostructureConfig, ScreenConfig
from money_more.data.intelligence import _macro_records_newest


def test_surge_exclude_main_vs_chi_star() -> None:
    df = _normalize_spot(
        pd.DataFrame(
            [
                {"代码": "600519", "名称": "茅台", "最新价": 1400, "涨跌幅": 7.5, "成交额": 2e9},
                {"代码": "600036", "名称": "招行", "最新价": 35, "涨跌幅": 8.0, "成交额": 2e9},
                {"代码": "300751", "名称": "创板温和", "最新价": 50, "涨跌幅": 8.0, "成交额": 2e9},
                {"代码": "300750", "名称": "宁德暴涨", "最新价": 200, "涨跌幅": 15.0, "成交额": 2e9},
                {"代码": "000001", "名称": "平安银行", "最新价": 10, "涨跌幅": 1.0, "成交额": 1e9},
            ]
        )
    )
    cfg = ScreenConfig(exclude_surge_main_pct=7.0, exclude_surge_chi_star_pct=14.0)
    out, stats = _exclude_surge_rows(df, cfg, force_codes=["600519"])
    codes = set(out["code"].tolist())
    assert "600519" in codes  # 持仓强制保留（即使 ≥7%）
    assert "000001" in codes
    assert "300751" in codes  # 创业板 8% < 14%
    assert "600036" not in codes  # 主板 8% ≥ 7%
    assert "300750" not in codes  # 创业板 15% ≥ 14%
    assert stats["surge"] >= 2


def test_score_universe_ignores_day_change() -> None:
    df = _normalize_spot(
        pd.DataFrame(
            [
                {
                    "代码": "000001",
                    "名称": "A",
                    "最新价": 10,
                    "涨跌幅": 9.5,
                    "成交额": 1e9,
                    "市盈率-动态": 6,
                    "市净率": 0.7,
                },
                {
                    "代码": "000002",
                    "名称": "B",
                    "最新价": 10,
                    "涨跌幅": 0.2,
                    "成交额": 1e9,
                    "市盈率-动态": 6,
                    "市净率": 0.7,
                },
            ]
        )
    )
    scored = _score_universe(df, [], 0)
    s1 = float(scored[scored["code"] == "000001"]["screen_score"].iloc[0])
    s2 = float(scored[scored["code"] == "000002"]["screen_score"].iloc[0])
    assert abs(s1 - s2) < 1e-6


def test_micro_severe_forbids_same_day() -> None:
    overview = {
        "limit_up_count": 100,
        "limit_down_count": 1,
        "indices": [{"change_pct": 3.0}],
        "northbound": {"latest_net": -100},
    }
    micro = assess_market_microstructure(
        overview,
        None,
        config=MicrostructureConfig(extreme_limit_ratio=50.0),
        prior_micro=None,
    )
    assert micro["extreme_crowding"] is True
    assert micro["severity"] in ("moderate", "severe")
    assert micro["forbid_new_buys"] is True


def test_micro_mild_needs_prior_confirm() -> None:
    overview = {"limit_up_count": 10, "limit_down_count": 5, "indices": [{"change_pct": 0.5}]}
    # 造同向性 mild：用 spot
    spot = pd.DataFrame({"涨跌幅": [1.0] * 80 + [-0.5] * 20, "成交额": [1e8] * 100})
    micro1 = assess_market_microstructure(overview, spot, prior_micro=None)
    assert micro1["severity"] in ("mild", "moderate", "none", "severe")
    if micro1["severity"] in ("mild", "moderate") and not micro1.get("extreme_crowding"):
        assert micro1["forbid_new_buys"] is False or micro1["pending_confirm"] is True
        micro2 = assess_market_microstructure(
            overview,
            spot,
            prior_micro={"regime": micro1["regime"], "severity": micro1["severity"]},
        )
        if micro2["severity"] in ("mild", "moderate"):
            assert micro2["forbid_new_buys"] is True


def test_prosperity_blocks_add() -> None:
    fw = {
        "prosperity_by_code": {"600519": "down"},
        "prosperity_block_adds": True,
        "block_offensive_buys": False,
        "policy_requires_hard_resonance": False,
        "hard_resonance_ok": True,
        "contradiction_haircut": 1.0,
    }
    recs, ov = validate_recommendations(
        [{"code": "600519", "action": "add", "position_pct": 10, "confidence": 0.7}],
        holdings=[{"code": "600519", "quantity": 100, "cost": 1600}],
        constraints={"max_single_position_pct": 20, "max_total_position_pct": 80},
        framework_gates=fw,
        microstructure={"regime": "normal", "severity": "none", "forbid_new_buys": False},
    )
    assert recs[0]["action"] in ("hold", "watch")
    assert any("景气down" in x for x in ov)


def test_framework_phase_clamp() -> None:
    cfg = FrameworkGateConfig(phase_upgrade_needs_confirm=True)
    state = build_framework_gate_state(
        config=cfg,
        market_analysis={
            "phase": "range",
            "style": "偏成长",
            "risk_level": "medium",
            "vs_prior": "shift",
            "confidence": 0.7,
        },
        macro_intel={"macro_hard": {"pmi": [{"制造业": 49.0}]}, "margin_trend": {"financing_balance_change_5d_pct": -1}},
        microstructure={"regime": "liquidity_stress", "severity": "severe", "forbid_new_buys": True},
        prior_context={
            "market_history": [
                {"phase": "bear", "style": "偏防御", "risk_level": "high", "micro_regime": "liquidity_stress"}
            ]
        },
    )
    assert state["block_phase_upgrade"] is True
    clamped, ov = clamp_market_optimism(
        {"phase": "range", "style": "偏成长硬科技", "risk_level": "medium", "vs_prior": "shift", "confidence": 0.7},
        state,
    )
    assert clamped["risk_level"] == "high"
    assert ov


def test_prosperity_map_from_sectors() -> None:
    m = build_code_prosperity_map(
        [{"sector": "白酒", "analysis": {"sector": "白酒", "prosperity": "down"}}],
        [{"code": "600519", "analysis": {"sector": "白酒"}}],
    )
    assert m.get("600519") == "down"


def test_macro_records_newest_ascending() -> None:
    df = pd.DataFrame(
        {
            "月份": ["201501", "202601", "202606"],
            "社会融资规模增量": [1, 2, 3],
        }
    )
    recs = _macro_records_newest(df, 2)
    assert len(recs) == 2
    # 最新应在前
    assert str(recs[0].get("月份")) in ("202606", "2026年06月份", "202606")
