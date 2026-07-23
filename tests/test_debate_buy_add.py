"""buy/add 全员辩论。"""

from __future__ import annotations

from money_more.analysis.debate import (
    apply_debate_to_recommendations,
    codes_needing_debate,
    run_buy_add_debates,
)


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyze_json(self, system, payload, **kwargs):
        code = str(payload.get("code") or "")
        self.calls.append(code)
        return {
            "code": code,
            "bull_case": "多",
            "bear_case": "空",
            "referee": "draw",
            "confidence_haircut": 0.05,
            "key_contradiction": "分歧",
            "decision_hint": "watch",
        }


def test_codes_needing_debate_only_buy_add():
    recs = [
        {"code": "300408", "action": "buy"},
        {"code": "601899", "action": "hold"},
        {"code": "002475", "action": "add"},
        {"code": "300408", "action": "buy"},  # dup
        {"code": "000001", "action": "sell"},
    ]
    assert codes_needing_debate(recs) == ["300408", "002475"]


def test_run_buy_add_debates_covers_all_buy_add_not_just_top_score():
    llm = _FakeLLM()
    analyses = [
        {"code": "601899", "factor_scorecard": {"total_score": 90}, "analysis": {}},
        {"code": "300408", "factor_scorecard": {"total_score": 55}, "analysis": {}},
        {"code": "002475", "factor_scorecard": {"total_score": 40}, "analysis": {}},
    ]
    recs = [
        {"code": "300408", "action": "buy", "confidence": 0.8},
        {"code": "002475", "action": "add", "confidence": 0.7},
        {"code": "601899", "action": "hold", "confidence": 0.6},
    ]
    debates = run_buy_add_debates(llm, analyses, recs)
    assert set(debates) == {"300408", "002475"}
    assert set(llm.calls) == {"300408", "002475"}
    overrides = apply_debate_to_recommendations(recs, debates)
    assert recs[0]["debate_status"] == "debated"
    assert recs[1]["debate_status"] == "debated"
    assert recs[2]["debate_status"] == "n/a"
    assert any("haircut" in o for o in overrides)
