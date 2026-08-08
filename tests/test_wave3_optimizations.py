"""第三波：东财直连开关、auto_sector 观察/升权、景气拐点豁免。"""

from __future__ import annotations

import os

from money_more.analysis.decision_validator import validate_recommendations
from money_more.analysis.framework_gates import build_code_inflection_map, build_framework_gate_state
from money_more.analysis.pipeline import DecisionPipeline
from money_more.config import FrameworkGateConfig
from money_more.data.ak_direct import eastmoney_direct_session, set_eastmoney_force_direct


def test_eastmoney_direct_clears_proxy_env() -> None:
    set_eastmoney_force_direct(True)
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
    os.environ["https_proxy"] = "http://127.0.0.1:9"
    with eastmoney_direct_session():
        assert "HTTP_PROXY" not in os.environ
        assert "https_proxy" not in os.environ
        assert os.environ.get("NO_PROXY") == "*"
    assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:9"
    assert os.environ.get("https_proxy") == "http://127.0.0.1:9"
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("https_proxy", None)


def test_auto_sector_promote_vs_observe() -> None:
    macro = {
        "sector_money_flow": {
            "top_inflow": [{"板块": "半导体"}, {"板块": "煤炭"}],
            "top_gainers": [{"板块": "半导体"}, {"板块": "旅游"}],
        },
        "narrative_radar": {"summary": "市场热议煤炭供需"},
    }
    meta = DecisionPipeline._auto_sectors_from_flow(macro, ["银行"], limit=3)
    assert "半导体" in meta["promote"]  # 双榜
    assert "煤炭" in meta["promote"]  # 叙事重叠
    assert "旅游" in meta["observe"] or "旅游" in meta["all"]
    assert "银行" not in meta["all"]


def test_inflection_exempts_prosperity_block() -> None:
    fw = build_framework_gate_state(
        config=FrameworkGateConfig(prosperity_block_adds=True),
        market_analysis={},
        macro_intel={},
        microstructure={},
        prior_context={},
        sector_analyses=[
            {
                "sector": "半导体",
                "analysis": {
                    "sector": "半导体",
                    "prosperity": "down",
                    "inflection_signal": True,
                    "inflection_evidence": ["库存去化+报价回升"],
                },
            }
        ],
        stock_analyses=[
            {"code": "600000", "analysis": {"sector": "半导体", "prosperity": "down"}}
        ],
    )
    assert fw["prosperity_by_code"].get("600000") == "down"
    assert fw["inflection_by_code"].get("600000", {}).get("signal") is True
    # 本用例只测景气拐点；关掉其它框架闸干扰
    fw["hard_resonance_ok"] = True
    fw["policy_requires_hard_resonance"] = False
    fw["block_offensive_buys"] = False

    recs, overrides = validate_recommendations(
        [{"code": "600000", "action": "buy", "position_pct": 5, "confidence": 0.6}],
        holdings=[],
        constraints={"max_single_position_pct": 20, "max_total_position_pct": 80},
        framework_gates=fw,
        allowed_codes={"600000"},
    )
    assert recs[0]["action"] == "buy"
    assert any("拐点豁免" in o for o in overrides)


def test_inflection_requires_evidence() -> None:
    inf = build_code_inflection_map(
        [{"analysis": {"sector": "银行", "inflection_signal": True, "inflection_evidence": []}}],
        [{"code": "601398", "analysis": {"sector": "银行"}}],
    )
    assert "601398" not in inf
