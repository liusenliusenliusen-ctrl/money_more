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


def test_w1_paper_holdings_stay_out_of_advice() -> None:
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
    assert by_code["000725"]["action"] == "watch"
    assert "300059" not in by_code
    assert not any("纸面持仓" in o for o in overs)


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
    assert "000725" not in by_code


def test_w1_a3_copy_empty_book_not_paper_hold() -> None:
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
                {"code": "600519", "action": "watch", "confidence": 0.4, "rationale": "深度池观察"},
            ],
        }
    )
    text = "\n".join(lines)
    assert "无持仓调仓建议" in text
    assert "须 hold/add/sell" not in text
    assert "000725" not in text


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


def test_review_failure_does_not_mark_llm_degraded() -> None:
    result: dict = {"data_quality": {}}
    DecisionPipeline._note_review_failed(result, "复盘失败(主结论已保留): cannot access local variable 'dedupe_pending_by_code'")
    dq = result["data_quality"]
    assert dq["review_failed"] is True
    assert "dedupe_pending_by_code" in str(dq.get("review_note") or "")
    assert dq.get("llm_degraded") is not True
    assert not (result.get("llm_stage_errors") or [])
    from money_more.report.writer import render_conclusion_card, render_run_status_section

    card = "\n".join(render_conclusion_card({"data_quality": dq, "run_date": "2026-09-11"}))
    assert "分析降级" not in card
    assert "复盘未完成" in card
    status = "\n".join(render_run_status_section({"data_quality": dq, "run_date": "2026-09-11"}))
    assert "部分 LLM 阶段已降级" not in status
    assert "复盘失败" in status


def test_sanitize_drops_company_names_and_fills_from_code() -> None:
    from money_more.analysis.sector_map import sanitize_sector_label
    from money_more.analysis.wave2_enrich import enrich_sector_link, build_sector_coverage

    assert sanitize_sector_label("东山精密") is None
    assert sanitize_sector_label("长鑫科技") is None
    assert sanitize_sector_label("药明康德") is None
    assert sanitize_sector_label("东山精密", code="600519") == "白酒"
    assert sanitize_sector_label("建材") == "建材"
    assert sanitize_sector_label("玻纤") == "建材"
    assert sanitize_sector_label("玻璃纤维") == "建材"
    assert sanitize_sector_label(None, code="600176") == "建材"
    assert sanitize_sector_label("交通运输") == "交通运输"
    assert sanitize_sector_label("航空运输") == "交通运输"
    assert sanitize_sector_label("航空装备") == "军工"
    assert sanitize_sector_label("建筑材料") == "建材"
    assert sanitize_sector_label("建筑装饰") == "建筑"
    assert sanitize_sector_label("化学制药") == "医药"
    assert sanitize_sector_label("基础化工") == "化工"
    assert sanitize_sector_label("消费电子") == "电子"
    assert sanitize_sector_label("钢铁") == "钢铁"
    assert sanitize_sector_label("农林牧渔") == "农林牧渔"
    assert sanitize_sector_label("电子元件-光学光电子-LED") == "元件"
    link, _ = enrich_sector_link({"code": "002384", "sector_tag": "东山精密", "action": "watch"})
    assert not is_known_sector_label(str(link.get("sector") or "东山精密")) or link.get("sector") != "东山精密"
    assert link.get("sector") != "东山精密"
    cov = build_sector_coverage([], [], deep_codes=["600519", "600036", "600276"])
    names = {c["sector"] for c in cov}
    assert "白酒" in names
    assert "银行" in names
    assert "医药" in names


def test_macro_news_noise_filter() -> None:
    from money_more.data.tushare_source import filter_macro_news_noise, is_macro_news_noise

    assert is_macro_news_noise({"title": "洗衣机/笔记本什么值得买"}) is True
    assert is_macro_news_noise({"title": "华发股份接待日暨投资者关系活动"}) is True
    assert is_macro_news_noise({"title": "央行宣布降准 0.5 个百分点"}) is False
    kept, dropped = filter_macro_news_noise(
        [
            {"title": "笔记本值得买清单"},
            {"title": "政治局会议定调"},
            {"title": "某公司投资者关系活动记录表"},
        ]
    )
    assert dropped == 2
    assert len(kept) == 1


def test_verify_ledger_watch_reading_is_discipline() -> None:
    from money_more.analysis.verify_tracker import build_verify_priors, evaluate_verify_window
    from datetime import date

    row = evaluate_verify_window(
        {"run_date": "2026-07-01", "code": "300750", "action": "watch", "verify_in_days": 14},
        [100.0, 101.0, 99.0],
        date(2026, 8, 1),
    )
    assert row["verdict"] == "avoid_failed"
    # priors 只看 buy-like，watch 的 avoid_failed 不得变成禁开仓
    from money_more.analysis.verify_tracker import build_verify_priors

    priors = build_verify_priors(
        [{**row, "sector": "东山精密", "action": "watch", "verdict": "avoid_failed"}]
    )
    assert priors["forbid_sectors"] == []
    assert priors["confidence_mult"] == 1.0


def test_spot_source_plain_reports_overlay() -> None:
    from money_more.analysis.degrade_messages import spot_source_plain

    assert "overlay：PE 10/20" in spot_source_plain(
        "sina", {"overlay": True, "pe_ok": 10, "pb_ok": 9, "n": 20}
    )
    assert "PE/PB 未补上" in spot_source_plain("sina", {"overlay": True, "pe_ok": 0, "n": 20})
    assert "无 PE/PB overlay" in spot_source_plain("sina", {"overlay": False, "n": 20})


def test_paper_hold_excluded_from_buy_like() -> None:
    from money_more.analysis.verify_tracker import is_declared_buy_like, build_verify_priors

    paper = {"action": "hold", "position_pct": 0, "verdict": "miss", "sector": "元件", "run_date": "2026-09-11"}
    declared = {"action": "hold", "position_pct": 8.0, "verdict": "hit", "sector": "银行", "run_date": "2026-09-11"}
    buy = {"action": "buy", "position_pct": 0, "verdict": "miss", "sector": "通信", "run_date": "2026-09-11"}
    assert is_declared_buy_like(paper) is False
    assert is_declared_buy_like(declared) is True
    assert is_declared_buy_like(buy) is True
    priors = build_verify_priors([paper, paper, paper])
    assert priors["forbid_sectors"] == []
    assert priors["confidence_mult"] == 1.0


def test_compact_stock_llm_payload_trims_history_series() -> None:
    from money_more.analysis.context_builder import compact_stock_llm_payload

    out = compact_stock_llm_payload(
        {
            "intelligence_digest": {
                "executive_summary": "x" * 20,
                "unused_blob": "y" * 5000,
                "headline_themes": list("abcdefghi"),
            },
            "market_context": {"phase": "range", "summary": "s" * 800, "long_table": [1] * 99},
            "prior_stock_series": [
                {"run_date": "2026-08-01", "analysis": {"research_rating": "hold", "summary": "z" * 400, "raw": "n" * 999}}
            ]
            * 8,
            "past_lessons": list(range(20)),
        }
    )
    assert "unused_blob" not in (out.get("intelligence_digest") or {})
    assert len(out["market_context"]["summary"]) < 800
    assert len(out["prior_stock_series"]) <= 3
    assert "raw" not in out["prior_stock_series"][0]
    assert len(out["past_lessons"]) <= 4
