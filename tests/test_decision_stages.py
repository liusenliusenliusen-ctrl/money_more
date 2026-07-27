"""决策分阶段轨迹与终局 portfolio_summary。"""

from __future__ import annotations

from money_more.analysis.decision_stages import (
    build_decision_stages,
    build_final_portfolio_summary,
    build_research_stage,
    snapshot_recommendations,
)
from money_more.report.writer import render_conclusion_card, render_decision_stages_section


def test_complete_coverage_and_synthesis_audit():
    from money_more.analysis.decision_stages import (
        build_decision_stages,
        build_synthesis_audit,
        complete_stage_coverage,
    )

    research = [
        {"code": "300750", "research_rating": "strong_buy"},
        {"code": "603893", "research_rating": "buy"},
        {"code": "000938", "research_rating": "buy"},
    ]
    draft = [{"code": "300750", "action": "buy", "position_pct": 5, "confidence": 0.5}]
    debated = [{"code": "300750", "action": "buy", "position_pct": 5, "debate_status": "debated"}]
    risked = [{"code": "300750", "action": "watch", "position_pct": 0}]
    d2, b2, r2 = complete_stage_coverage(research, draft, debated, risked)
    assert len(d2) == 3
    by = {x["code"]: x for x in d2}
    assert by["300750"]["selection"] == "selected"
    assert by["603893"]["selection"] == "not_selected"
    assert by["603893"]["action"] == "watch"

    audit = build_synthesis_audit(
        multi_agent_drafts={
            "deepseek": {
                "recommendations": [
                    {"code": "300750", "action": "buy", "position_pct": 8},
                    {"code": "603893", "action": "buy", "position_pct": 5},
                    {"code": "000938", "action": "watch"},
                ]
            },
            "cursor": {
                "recommendations": [
                    {"code": "300750", "action": "watch"},
                    {"code": "603893", "action": "watch"},
                ]
            },
        },
        portfolio_draft=draft,
        meta={"primary": "deepseek", "secondary": "cursor"},
    )
    assert audit is not None
    assert audit["agent_buy_counts"]["deepseek"] == 2
    assert audit["agent_buy_counts"]["cursor"] == 0
    assert "300750" in audit["synthesized_buys"]
    assert "603893" in audit["dropped_buys"]

    stages = build_decision_stages(
        research=research,
        portfolio_draft=draft,
        after_debate=debated,
        after_risk=risked,
        synthesis_audit=audit,
    )
    assert stages["synthesis_audit"]["dropped_buys"] == ["603893"]
    assert any(r.get("selection") == "not_selected" for r in stages["portfolio_draft"])

    result = {
        "decision_stages": stages,
        "decision_summary": {"holdings_basis": {"is_empty": True}},
        "recommendations": [{"code": "300750", "action": "watch", "rationale": "风控"}],
        "stocks": [],
        "market": {"analysis": {}},
        "intelligence": {"digest": {}},
        "sectors": [],
        "multi_agent": {
            "enabled": True,
            "meta": {"primary": "deepseek", "secondary": "cursor", "synthesizer": "synthesizer"},
        },
        "multi_agent_drafts": {
            "deepseek": {
                "recommendations": [
                    {"code": "300750", "action": "buy", "position_pct": 8},
                    {"code": "603893", "action": "buy", "position_pct": 5},
                ]
            },
            "cursor": {"recommendations": [{"code": "300750", "action": "watch"}]},
        },
    }
    text = "\n".join(render_decision_stages_section(result))
    assert "观察·未入选" in text
    assert "综合取舍审计" in text
    assert "603893" in text
    # A3 / recommendations 仍只有终局票，不应因 coverage 膨胀
    assert len(result["recommendations"]) == 1
    stocks = [
        {
            "code": "600519",
            "analysis": {
                "code": "600519",
                "name": "贵州茅台",
                "research_rating": "buy",
                "confidence": 0.7,
                "summary": "质地好",
            },
            "factor_scorecard": {"total_score": 0.8},
        }
    ]
    research = build_research_stage(stocks)
    assert research[0]["research_rating"] == "buy"
    assert research[0]["name"] == "贵州茅台"

    draft = snapshot_recommendations(
        [{"code": "600519", "action": "buy", "position_pct": 10, "confidence": 0.6}]
    )
    assert draft[0]["action"] == "buy"
    assert draft[0]["position_pct"] == 10


def test_final_summary_after_risk_not_draft_prose():
    # 研究/草案可能说「分批建仓」，终局空仓时摘要必须反映无可执行开仓
    summary = build_final_portfolio_summary(
        [
            {"code": "600519", "action": "watch", "position_pct": 0},
            {"code": "300750", "action": "watch", "position_pct": 0},
        ],
        holdings_basis={"is_empty": True, "codes": []},
        overrides=["600519: 微观结构liquidity_stress禁止新买 → watch"],
        microstructure={"regime": "liquidity_stress"},
        data_quality={"score": 1.0, "degraded": False},
    )
    assert "无可执行新开仓" in summary
    assert "liquidity_stress" in summary
    assert "分批建仓" not in summary
    assert "空仓" in summary


def test_final_summary_with_deployable_buys():
    summary = build_final_portfolio_summary(
        [
            {"code": "600519", "action": "buy", "position_pct": 8},
            {"code": "300750", "action": "watch", "position_pct": 0},
        ],
        holdings_basis={"is_empty": True, "codes": []},
        overrides=[],
        microstructure={"regime": "normal"},
        data_quality={"score": 1.0},
    )
    assert "可执行开仓" in summary
    assert "600519" in summary
    assert "8%" in summary


def test_decision_stages_payload_and_report_render():
    stages = build_decision_stages(
        research=[
            {
                "code": "600519",
                "name": "贵州茅台",
                "research_rating": "buy",
                "confidence": 0.7,
            }
        ],
        portfolio_draft=[
            {"code": "600519", "action": "buy", "position_pct": 10, "confidence": 0.6}
        ],
        after_debate=[
            {
                "code": "600519",
                "action": "buy",
                "position_pct": 10,
                "confidence": 0.5,
                "referee": "bull",
            }
        ],
        after_risk=[
            {"code": "600519", "action": "watch", "position_pct": 0, "confidence": 0.5}
        ],
        overrides=["600519: 微观结构liquidity_stress禁止新买 → watch"],
        draft_portfolio_summary="分批建仓茅台",
    )
    stages["final_portfolio_summary"] = build_final_portfolio_summary(
        stages["after_risk"],
        holdings_basis={"is_empty": True},
        overrides=stages["overrides"],
        microstructure={"regime": "liquidity_stress"},
    )
    result = {
        "decision_stages": stages,
        "decision_summary": {
            "portfolio_summary": stages["final_portfolio_summary"],
            "portfolio_summary_draft": "分批建仓茅台",
            "holdings_basis": {"is_empty": True, "codes": []},
        },
        "recommendations": [
            {"code": "600519", "action": "watch", "confidence": 0.5, "rationale": "风控"}
        ],
        "stocks": [
            {"code": "600519", "analysis": {"code": "600519", "name": "贵州茅台"}}
        ],
        "market": {"analysis": {"phase_label": "震荡", "style_label": "均衡", "risk_level": "medium"}},
        "intelligence": {"digest": {}},
        "sectors": [],
    }
    block = render_decision_stages_section(result)
    text = "\n".join(block)
    assert "步骤说明" in text
    assert "① 个股研究" in text or "①个股研究" in text or "**① 个股研究**" in text
    assert "综合" in text
    assert "①研究评级" in text
    assert "④风控终局" in text
    assert "分批建仓" in text  # 草案对照可见
    assert "无可执行新开仓" in text or "终局组合摘要" in text
    # 草案摘要在终局摘要之前
    assert text.index("②草案摘要") < text.index("④终局组合摘要")

    card = "\n".join(render_conclusion_card(result))
    assert "### B. 推理链" in card
    assert "#### B2. 个股决策链" in card
    assert "本步做什么" in card
    assert "④风控终局" in card
    assert "分批建仓茅台" in card
    assert card.index("②草案摘要") < card.index("④终局组合摘要")
    # 动作区应体现终局 watch，而非把草案当主指令标题
    assert "#### A3. 动作：怎么做（④风控终局）" in card


def test_stock_decision_chain_and_slim_recommendations():
    from money_more.report.writer import render_daily_report

    stages = build_decision_stages(
        research=[
            {
                "code": "300750",
                "name": "宁德时代",
                "research_rating": "buy",
                "confidence": 0.7,
                "summary": "质地好",
            }
        ],
        portfolio_draft=[
            {
                "code": "300750",
                "action": "buy",
                "position_pct": 5,
                "confidence": 0.6,
                "rationale": "分批建仓",
            }
        ],
        after_debate=[
            {
                "code": "300750",
                "action": "buy",
                "position_pct": 5,
                "confidence": 0.55,
                "referee": "bull",
            }
        ],
        after_risk=[
            {"code": "300750", "action": "watch", "position_pct": 0, "confidence": 0.55}
        ],
        overrides=["300750: 微观结构liquidity_stress禁止新买 → watch"],
        draft_portfolio_summary="分批建仓宁德",
    )
    result = {
        "run_date": "2026-07-23",
        "decision_stages": stages,
        "decision_summary": {
            "portfolio_summary": "终局无可执行新开仓",
            "portfolio_summary_draft": "分批建仓宁德",
            "holdings_basis": {"is_empty": True, "codes": []},
            "market_context": "流动性压力",
        },
        "validation_overrides": ["300750: 微观结构liquidity_stress禁止新买 → watch"],
        "debates": {
            "300750": {
                "referee": "bull",
                "confidence_haircut": 0.05,
                "decision_hint": "buy",
                "bull_case": "龙头份额稳",
                "bear_case": "估值不便宜",
            }
        },
        "stocks": [
            {
                "code": "300750",
                "analysis": {
                    "code": "300750",
                    "name": "宁德时代",
                    "research_rating": "buy",
                    "quality": "high",
                    "valuation": "fair",
                    "investment_thesis": "动力电池龙头",
                    "summary": "中长线可跟踪",
                    "confidence": 0.7,
                },
                "factor_scorecard": {"total_score": 0.72, "signal": "lean_buy", "scores": {"quality": 0.8}},
            }
        ],
        "recommendations": [
            {
                "code": "300750",
                "action": "watch",
                "confidence": 0.55,
                "position_pct": 0,
                "rationale": "微观结构压力下禁止新买",
                "sector_tag": "新能源",
                "debate_status": "debated",
                "debate": {
                    "referee": "bull",
                    "confidence_haircut": 0.05,
                    "bull_case": "龙头份额稳",
                    "bear_case": "估值不便宜",
                },
                "invalidation": "装机份额显著下滑",
                "key_risk": "价格战",
            }
        ],
        "market": {"analysis": {}},
        "intelligence": {"digest": {}},
        "sectors": [],
    }
    md = render_daily_report(result)
    assert "## 详细论证" in md
    assert "### A. 展开主结论" in md
    assert "### B. 展开推理链" in md
    assert "### C. 展开侧栏" in md
    assert "#### B2. 个股决策链" in md
    assert "###### ① 研究（基本面 / 赔率 / 叙事）" in md
    assert "###### ② 组合草案" in md
    assert "###### ③ 多空辩论" in md
    assert "###### ④ 风控终局" in md
    assert "动力电池龙头" in md
    assert "分批建仓" in md
    assert "裁判" in md and "bull" in md
    assert "微观结构liquidity_stress禁止新买" in md
    assert "#### A3. 动作：怎么做（索引" in md
    assert "## 4. 买卖建议" not in md
    assert "## D. 复盘与经验" not in md
    assert "## 附录：模拟账本" not in md
    assert "①买入" in md and "④观察" in md
    from money_more.report.writer import render_review_report, render_sim_report

    review_md = render_review_report(result)
    assert "# money_more 复盘与经验" in review_md
    sim_md = render_sim_report(result)
    assert "# money_more 模拟账本" in sim_md
    # A3 只做索引，不堆辩论；失效/纪律在 B2④
    idx_a3 = md.index("#### A3. 动作：怎么做（索引")
    idx_b = md.index("### B. 展开推理链")
    section_a3 = md[idx_a3:idx_b]
    assert "多头" not in section_a3
    assert "终局一览" in section_a3
    assert "**失效条件**: 装机份额显著下滑" in md
