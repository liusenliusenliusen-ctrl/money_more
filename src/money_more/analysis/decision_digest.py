"""将报告中的关键数值做成可机读的决策摘要，便于回测与对比。"""

from __future__ import annotations

from typing import Any


def build_decision_digest(result: dict[str, Any]) -> dict[str, Any]:
    market = (result.get("market") or {}).get("analysis") or {}
    dq = result.get("data_quality") or {}
    recs = []
    for r in result.get("recommendations") or []:
        sc = r.get("factor_scorecard") or {}
        recs.append(
            {
                "code": r.get("code"),
                "action": r.get("action"),
                "confidence": r.get("confidence"),
                "position_pct": r.get("position_pct"),
                "target_price": r.get("target_price"),
                "stop_loss": r.get("stop_loss"),
                "factor_total": sc.get("total_score"),
                "factor_signal": sc.get("signal"),
                "debate_referee": (r.get("debate") or {}).get("referee"),
                "sector_tag": r.get("sector_tag"),
                "invalidation": r.get("invalidation"),
            }
        )
    return {
        "run_date": result.get("run_date"),
        "prompt_version": result.get("prompt_version"),
        "market_phase": market.get("phase"),
        "market_style": market.get("style"),
        "risk_level": market.get("risk_level"),
        "data_quality_score": dq.get("score"),
        "degraded": dq.get("degraded"),
        "recommendations": recs,
        "risk_check_ok": (result.get("risk_check") or {}).get("ok"),
        "validation_override_count": len(result.get("validation_overrides") or []),
        "factor_weights_adapted": result.get("factor_weights_adapted"),
    }
