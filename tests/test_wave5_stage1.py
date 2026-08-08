"""第五波阶段一：止血 + 工程修（线程安全 / 截断检测 / 文案对齐 / DQ 拆层）。"""

from __future__ import annotations

import json
import threading
from typing import Any

from money_more.analysis.decision_validator import validate_recommendations
from money_more.analysis.wave2_enrich import refresh_sector_link_rationale
from money_more.data import ak_direct
from money_more.llm.providers.openai_compat import (
    LengthTruncatedError,
    _compact_user_payload,
)


def test_ak_direct_nested_thread_safe() -> None:
    """C5：并发进出 bypass 不应让 env 处于半清状态。"""
    errors: list[str] = []

    def worker() -> None:
        try:
            for _ in range(50):
                with ak_direct.eastmoney_direct_session():
                    # 内层再嵌套一层
                    with ak_direct.eastmoney_direct_session():
                        pass
        except Exception as exc:  # pragma: no cover
            errors.append(str(exc))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert ak_direct._DIRECT_DEPTH == 0


def test_compact_user_payload_truncates_long_strings() -> None:
    payload = json.dumps(
        {
            "stocks": [{"rationale": "x" * 3000} for _ in range(20)],
            "big_text": "y" * 5000,
        }
    )
    out = _compact_user_payload(payload)
    assert len(out) < len(payload)
    assert "截断" in out or "省略" in out


def test_length_truncated_error_exists() -> None:
    err = LengthTruncatedError("finish=length")
    assert "finish=length" in str(err)


def test_sector_link_refresh_aligns_with_final_action() -> None:
    """C7 + A0-2：门禁翻成 watch 后，sector_link 不得仍写 research buy → buy，
    且 rationale 里的「轻仓买入」类文案要被清掉。"""
    rec: dict[str, Any] = {
        "code": "600519",
        "action": "buy",
        "position_pct": 10.0,
        "rationale": "可轻仓买入试探",
    }
    link = refresh_sector_link_rationale(rec, research_by_code={"600519": {"research_rating": "buy"}})
    assert "research buy → buy" in link["action_rationale_vs_research"]

    # 模拟门禁翻到 watch/0%
    rec["action"] = "watch"
    rec["position_pct"] = 0.0
    link = refresh_sector_link_rationale(rec, research_by_code={"600519": {"research_rating": "buy"}})
    txt = link["action_rationale_vs_research"]
    assert "→ buy" not in txt
    assert "→ watch" in txt
    assert "轻仓买入" not in rec["rationale"]


def test_validate_recommendations_refresh_rationale_end_to_end() -> None:
    recs = [
        {
            "code": "600519",
            "action": "buy",
            "position_pct": 10.0,
            "confidence": 0.6,
            "rationale": "小仓位买入试探",
        }
    ]
    out, overrides = validate_recommendations(
        recs,
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        framework_gates={"contradiction_active": True, "block_offensive_buys": True},
        research_by_code={"600519": {"research_rating": "buy"}},
    )
    assert out[0]["action"] == "watch"
    assert out[0]["position_pct"] == 0.0
    txt = (out[0].get("sector_link") or {}).get("action_rationale_vs_research") or ""
    assert "→ watch" in txt
    assert "小仓位买入" not in out[0]["rationale"]


def test_dq_split_research_fields_on_tushare_fail() -> None:
    """A0-4：Tushare 无权限时 research_score 应 < 1 且 score 被压到 ≤0.85。"""
    from money_more.analysis.pipeline import DecisionPipeline

    macro = {
        "errors": ["Tushare 重大新闻: 没有接口权限"],
        "policy_news": ["x"],
        "global_news": ["x"],
        "rss_telegraph": ["x"],
        "margin_trend": ["x"],
        "northbound_summary": {"x": 1},
        "northbound_freshness": {"stale": False},
        "sentiment_overview": {"aggregate": {"score_100": 50}},
        "economic_calendar": ["x"],
        "macro_hard_echo": ["x"],
        "tushare_macro_news": ["x"],  # 新闻通但权限错已在 errors
        "sector_money_flow": {"rows": [{"x": 1}]},
        "macro_hard": {"pmi": [{"x": 1}], "social_financing": [{"月份": "202606"}]},
        "global_liquidity": {"stance": "neutral"},
    }
    dq = DecisionPipeline._assess_data_quality(macro)
    assert dq["tushare_perm_issue"] is True
    assert dq["research_score"] < 1.0
    assert dq["score"] <= 0.85
    assert any("研究层降权" in str(dq.get("note", "")) for _ in [0])
