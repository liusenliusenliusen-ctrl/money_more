"""轻量多空辩论：仅对因子分最高的 Top-K 标的加一轮裁判。"""

from __future__ import annotations

from typing import Any

from money_more.llm.client import LLMClient

DEBATE_SYSTEM = """你是投资委员会裁判。给定一只股票的多空材料，输出简短辩论结论 JSON：
{
  "code": "6位代码",
  "bull_case": "看多要点（<=80字）",
  "bear_case": "看空要点（<=80字）",
  "referee": "bull|bear|draw",
  "confidence_haircut": 0.0-0.3,
  "key_contradiction": "主要矛盾一句话",
  "decision_hint": "buy|add|hold|watch|sell"
}
原则：证据不足时选 draw 并提高 haircut；禁止编造未提供的数据。"""


def run_top_k_debates(
    llm: LLMClient,
    stock_analyses: list[dict[str, Any]],
    *,
    top_k: int = 2,
    min_score: float = 55.0,
) -> dict[str, Any]:
    """返回 code -> debate 结果。"""
    ranked = []
    for s in stock_analyses:
        sc = s.get("factor_scorecard") or {}
        total = sc.get("total_score")
        if total is None:
            continue
        try:
            total_f = float(total)
        except (TypeError, ValueError):
            continue
        if total_f < min_score:
            continue
        ranked.append((total_f, s))
    ranked.sort(key=lambda x: x[0], reverse=True)
    selected = [s for _, s in ranked[:top_k]]

    out: dict[str, Any] = {}
    for s in selected:
        code = str(s.get("code") or "")
        payload = {
            "code": code,
            "analysis": s.get("analysis"),
            "factor_scorecard": s.get("factor_scorecard"),
            "cross_check": s.get("cross_check"),
            "hard_gates": s.get("hard_gates"),
            "history": (s.get("snapshot") or {}).get("history"),
            "fund_flow": (s.get("snapshot") or {}).get("fund_flow"),
        }
        try:
            debate = llm.analyze_json(
                DEBATE_SYSTEM,
                payload,
                temperature=0.2,
                required_keys=["code", "referee", "confidence_haircut", "decision_hint"],
                max_retries=1,
            )
            out[code] = debate
        except Exception as exc:
            out[code] = {"code": code, "error": str(exc), "referee": "draw", "confidence_haircut": 0.1}
    return out


def apply_debate_to_recommendations(
    recommendations: list[dict[str, Any]],
    debates: dict[str, Any],
) -> list[str]:
    """按辩论结果下调置信度 / 提示动作；返回 overrides。未辩论的买卖会打标。"""
    overrides: list[str] = []
    for rec in recommendations:
        code = str(rec.get("code") or "")
        action = str(rec.get("action") or "").lower()
        d = debates.get(code)
        if not d or d.get("error"):
            if action in ("buy", "add"):
                rec["debate_status"] = "undebated"
                overrides.append(f"{code}: 买卖建议未经多空辩论（undebated）")
            else:
                rec["debate_status"] = "n/a"
            continue
        rec["debate"] = d
        rec["debate_status"] = "debated"
        try:
            haircut = float(d.get("confidence_haircut") or 0)
        except (TypeError, ValueError):
            haircut = 0.0
        if haircut > 0 and rec.get("confidence") is not None:
            try:
                c0 = float(rec["confidence"])
                rec["confidence"] = round(max(0.05, c0 - haircut), 3)
                overrides.append(f"{code}: 辩论 haircut -{haircut} → conf={rec['confidence']}")
            except (TypeError, ValueError):
                pass
        hint = str(d.get("decision_hint") or "").lower()
        referee = str(d.get("referee") or "").lower()
        if referee == "bear" and action in ("buy", "add"):
            rec["action"] = "watch"
            rec["position_pct"] = 0
            overrides.append(f"{code}: 辩论裁判 bear → watch")
        elif hint == "watch" and action in ("buy", "add"):
            overrides.append(f"{code}: 辩论建议 watch（未强制，仅记录）")
    return overrides
