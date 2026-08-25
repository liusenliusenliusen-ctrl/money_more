"""8/25 复核后的 W1–W5：纸面仓 / 双确认 / 主题保底 / 复盘去重 / 降级≠首选。"""

from __future__ import annotations

from money_more.analysis.decision_validator import validate_recommendations
from money_more.analysis.framework_gates import (
    build_framework_gate_state,
    clamp_market_optimism,
    us_10y_blocks_optimism,
)
from money_more.analysis.market_microstructure import assess_market_microstructure
from money_more.analysis.pipeline import DecisionPipeline
from money_more.analysis.review_history import dedupe_pending_by_code
from money_more.analysis.sector_map import is_known_sector_label
from money_more.analysis.wave2_enrich import build_dimension_diff_table
from money_more.config import FrameworkGateConfig
from money_more.report.writer import render_conclusion_card


def _constraints() -> dict[str, float]:
    return {
        "max_single_position_pct": 20,
        "max_total_position_pct": 80,
        "stop_loss_pct": 15,
        "take_profit_pct": 40,
    }


def test_w1_paper_holdings_get_hold_not_watch() -> None:
    recs, overs = validate_recommendations(
        [{"code": "000725", "action": "watch", "confidence": 0.5, "position_pct": 0}],
        holdings=[],
        constraints=_constraints(),
        quotes={"000725": 5.75},
        allowed_codes={"000725", "300059"},
        paper_holdings=[
            {"code": "000725", "quantity": 100, "cost": 6.07},
            {"code": "300059", "quantity": 200, "cost": 20.36},
        ],
    )
    by_code = {r["code"]: r for r in recs}
    assert by_code["000725"]["action"] == "hold"
    assert "300059" in by_code  # 补全缺失纸面仓
    assert by_code["300059"]["action"] == "hold"
    assert any("纸面" in o for o in overs)


def test_w1_empty_real_book_still_blocks_random_hold() -> None:
    recs, _ = validate_recommendations(
        [{"code": "600519", "action": "hold", "confidence": 0.5, "position_pct": 5}],
        holdings=[],
        constraints=_constraints(),
        quotes={"600519": 1400},
        allowed_codes={"600519"},
        paper_holdings=[{"code": "000725", "quantity": 100, "cost": 6.0}],
    )
    by_code = {r["code"]: r for r in recs}
    assert by_code["600519"]["action"] == "watch"
    assert by_code["000725"]["action"] == "hold"


def test_w1_a3_copy_mentions_paper_when_real_empty() -> None:
    lines = render_conclusion_card(
        {
            "run_date": "2026-08-25",
            "market": {"analysis": {"phase": "range", "style": "均衡", "risk_level": "high"}},
            "decision_summary": {
                "holdings_basis": {
                    "is_empty": True,
                    "codes": [],
                    "paper_codes": ["000725", "300059"],
                }
            },
            "recommendations": [
                {"code": "000725", "action": "hold", "confidence": 0.4, "rationale": "纸面持仓"},
            ],
        }
    )
    text = "\n".join(lines)
    assert "纸面" in text
    assert "000725" in text
    assert "无持仓调仓建议" not in text


def test_w2_elevated_forbids_new_buys() -> None:
    out = assess_market_microstructure({"limit_down_count": 40})
    assert out["regime"] == "elevated"
    assert out["forbid_new_buys"] is True
    recs, overs = validate_recommendations(
        [{"code": "300750", "action": "buy", "confidence": 0.8, "position_pct": 10}],
        holdings=[],
        constraints=_constraints(),
        quotes={"300750": 200},
        allowed_codes={"300750"},
        microstructure=out,
        market_risk_level="high",
        global_liquidity={
            "us_10y": {"latest": 4.74, "change_20d_bp": 18, "change_60d_bp": 26}
        },
    )
    assert recs[0]["action"] == "watch"
    assert any("禁新买" in o or "禁新" in o for o in overs)


def test_w2_us10y_blocks_risk_downgrade_and_growth() -> None:
    macro = {
        "global_liquidity": {
            "us_10y": {"latest": 4.74, "change_20d_bp": 8, "change_60d_bp": 26}
        }
    }
    assert us_10y_blocks_optimism(macro)["blocked"] is True
    state = build_framework_gate_state(
        config=FrameworkGateConfig(),
        market_analysis={"phase": "range", "style": "偏成长硬科技", "risk_level": "medium"},
        macro_intel=macro,
        microstructure={"regime": "elevated", "severity": "mild", "forbid_new_buys": True},
        prior_context={"market_history": [{"phase": "range", "style": "均衡", "risk_level": "high"}]},
    )
    assert state["us_yield_blocks_optimism"] is True
    assert state["block_phase_upgrade"] is True
    clamped, ov = clamp_market_optimism(
        {"phase": "range", "style": "偏成长硬科技", "risk_level": "medium", "confidence": 0.7},
        state,
    )
    assert clamped["risk_level"] == "high"
    assert "防御" in str(clamped.get("style") or "")
    assert ov


def test_w4_pending_dedupe_keeps_latest_per_code() -> None:
    out = dedupe_pending_by_code(
        [
            {"stock_code": "600519", "run_date": "2026-07-01", "action": "add"},
            {"stock_code": "600519", "run_date": "2026-08-05", "action": "watch"},
            {"stock_code": "300750", "run_date": "2026-08-05", "action": "buy"},
        ]
    )
    by = {r["stock_code"]: r for r in out}
    assert len(out) == 2
    assert by["600519"]["run_date"] == "2026-08-05"


def test_w4_dimension_diff_drops_stock_names() -> None:
    assert is_known_sector_label("白酒") is True
    assert is_known_sector_label("半导体") is True
    assert is_known_sector_label("东山精密") is False
    assert is_known_sector_label("长鑫科技") is False
    table = build_dimension_diff_table(
        [
            {
                "date": "2026-07-13",
                "market": {"phase": "bear", "style": "防御", "risk_level": "high"},
                "sectors": [
                    {"sector": "白酒", "priority": "medium"},
                    {"sector": "东山精密", "priority": "high"},
                    {"sector": "长鑫科技", "priority": "high"},
                ],
            },
            {
                "date": "2026-08-05",
                "market": {"phase": "range", "style": "成长", "risk_level": "medium"},
                "sectors": [{"sector": "白酒", "priority": "low"}],
            },
        ],
        {"market": {"phase": "range"}, "sectors": []},
    )
    sector_names = {r.get("sector") for r in table if r.get("dimension") == "sector"}
    assert "白酒" in sector_names
    assert "东山精密" not in sector_names
    assert "长鑫科技" not in sector_names


def test_w5_research_zero_caps_connect_score() -> None:
    dq = DecisionPipeline._assess_data_quality(
        {
            "errors": ["Tushare 没有接口权限"],
            "policy_news": ["x"],
            "global_news": ["x"],
            "rss_telegraph": ["x"],
            "margin_trend": ["x"],
            "northbound_summary": {"x": 1},
            "northbound_freshness": {"stale": False},
            "sentiment_overview": {"aggregate": {"score_100": 50}},
            "economic_calendar": ["x"],
            "macro_hard_echo": ["x"],
            "tushare_macro_news": ["x"],
            "sector_money_flow": {"top_inflow": [{"板块": "白酒", "净流入": 1}]},
            "macro_hard": {"pmi": [{"制造业": 49.2}], "social_financing": [{"月份": "202606"}]},
            "global_liquidity": {"stance": "neutral"},
        }
    )
    assert dq["research_score"] == 0.0
    assert dq["score"] <= 0.55
    assert dq["degraded"] is True
    assert "连接分封顶" in str(dq.get("note") or "")


def test_w5_monthly_pmi_not_refired_same_print() -> None:
    prior = [{"branch_id": "pmi_contraction", "topic": "景气", "fact": "PMI收缩(49.2)", "value": 49.2}]
    fw = build_framework_gate_state(
        config=FrameworkGateConfig(),
        market_analysis={},
        macro_intel={"macro_hard": {"pmi": [{"制造业PMI": 49.2}]}},
        microstructure={},
        prior_context={"contradiction_branches": prior},
    )
    assert fw["contradiction_active"] is True
    assert fw["monthly_repeat_flags"]
    assert not any("PMI" in str(x) for x in (fw.get("hard_contradiction_flags") or []))
    pmi_branch = next(
        b for b in fw["contradiction_branches"] if b.get("branch_id") == "pmi_contraction"
    )
    assert pmi_branch.get("same_period") is True or pmi_branch.get("reactivated") is False
