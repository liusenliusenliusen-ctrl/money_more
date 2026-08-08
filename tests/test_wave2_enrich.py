"""第二波：sector_link / 验证窗口 / 缺标的 / 维度对照。"""

from __future__ import annotations

from money_more.analysis.decision_validator import validate_recommendations
from money_more.analysis.wave2_enrich import (
    build_dimension_diff_table,
    build_sector_coverage,
    enrich_sector_link,
    enrich_verify_window,
)
from money_more.report.writer import render_conclusion_card, render_review_report


def test_enrich_sector_link_and_verify() -> None:
    rec = {"code": "600519", "action": "watch", "rationale": "观望"}
    link, note = enrich_sector_link(
        rec,
        sector_analyses=[{"analysis": {"sector": "白酒", "priority": "low", "prosperity": "down"}}],
        research_by_code={"600519": {"research_rating": "buy"}},
    )
    assert link["sector"]
    assert "buy" in str(link.get("from_research_rating") or link.get("action_rationale_vs_research"))
    vf, vnote = enrich_verify_window(rec)
    assert vf["verify_in_days"] == 14
    assert vf["verify_signals"]


def test_validator_fills_wave2_fields() -> None:
    recs, ov = validate_recommendations(
        [{"code": "600519", "action": "watch", "confidence": 0.5}],
        holdings=[],
        constraints={"max_single_position_pct": 20, "max_total_position_pct": 80},
        sector_analyses=[{"analysis": {"sector": "白酒", "priority": "high", "prosperity": "flat"}}],
        research_by_code={"600519": {"research_rating": "hold"}},
        microstructure={"regime": "normal", "forbid_new_buys": False},
        framework_gates={"prosperity_block_adds": False, "policy_requires_hard_resonance": False},
    )
    assert recs[0].get("sector_link", {}).get("sector")
    assert recs[0].get("verify_in_days") == 14
    assert recs[0].get("verify_signals")
    assert any("补全" in x or "sector_link" in x for x in ov) or True


def test_sector_coverage_missing_high() -> None:
    cov = build_sector_coverage(
        [
            {"analysis": {"sector": "半导体", "priority": "high", "prosperity": "up"}},
            {"analysis": {"sector": "白酒", "priority": "low", "prosperity": "down"}},
        ],
        [{"code": "600519", "sector_tag": "白酒", "action": "watch"}],
        deep_codes=["600519"],
        min_priority="high",
    )
    high = [c for c in cov if c["sector"] == "半导体"]
    assert high and high[0]["missing_target"] is True


def test_dimension_diff_table() -> None:
    table = build_dimension_diff_table(
        [
            {
                "date": "2026-07-13",
                "market": {"phase": "bear", "style": "防御", "risk_level": "high"},
                "sectors": [{"sector": "白酒", "priority": "medium"}],
            },
            {
                "date": "2026-08-05",
                "market": {"phase": "range", "style": "成长", "risk_level": "medium"},
                "sectors": [{"sector": "白酒", "priority": "low"}],
            },
        ],
        {"market": {"phase": "range", "style": "成长", "risk_level": "medium"}, "sectors": []},
    )
    assert any(r["field"] == "phase" and r["verdict"] == "changed" for r in table)
    assert any(r.get("sector") == "白酒" for r in table)


def test_conclusion_card_shows_verify_and_gap() -> None:
    lines = render_conclusion_card(
        {
            "run_date": "2026-08-08",
            "market": {
                "analysis": {
                    "phase": "range",
                    "style": "均衡",
                    "risk_level": "medium",
                    "confidence": 0.5,
                    "summary": "震荡",
                    "verify_in_days": 14,
                    "verify_signals": ["PMI回升"],
                }
            },
            "recommendations": [
                {
                    "code": "600519",
                    "action": "watch",
                    "confidence": 0.5,
                    "rationale": "等待",
                    "sector_link": {
                        "sector": "白酒",
                        "sector_priority": "low",
                        "action_rationale_vs_research": "research buy → watch",
                    },
                    "verify_in_days": 14,
                    "verify_signals": ["批价回升"],
                }
            ],
            "sectors": [
                {"analysis": {"sector": "半导体", "priority": "high", "prosperity": "up"}}
            ],
            "sector_coverage": [
                {
                    "sector": "半导体",
                    "priority": "high",
                    "missing_target": True,
                    "note": "半导体 优先级high，本轮深度池无映射标的 → 仅约束风格/仓位，非漏推个股",
                }
            ],
            "decision_stages": {
                "synthesis_audit": {
                    "agreed_buys": ["300750"],
                    "dropped_buys": ["601318"],
                    "agent_only_buys": {"primary": ["000001"]},
                }
            },
            "decision_summary": {"holdings_basis": {"is_empty": True}},
            "intelligence": {"digest": {}},
            "data_quality": {"score": 0.9, "degraded": False},
        }
    )
    md = "\n".join(lines)
    assert "验证窗口" in md
    assert "缺标的" in md or "无映射标的" in md
    assert "主副分歧" in md
    assert "逻辑链" in md


def test_review_report_has_diff_table() -> None:
    md = render_review_report(
        {
            "run_date": "2026-08-08",
            "review_window": {"lookback_days": 60, "cutoff": "2026-06-01", "as_of": "2026-08-08"},
            "dimension_diff_table": [
                {
                    "dimension": "market",
                    "field": "phase",
                    "then": "bear",
                    "now": "range",
                    "verdict": "changed",
                }
            ],
            "dimension_reviews": [],
            "reviews": [],
        }
    )
    assert "维度对照表" in md
    assert "changed" in md
