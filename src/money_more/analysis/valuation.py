"""估值分位：中长线定价锚，基于历史 PE/PB 百分位 + 股息率。"""

from __future__ import annotations

from typing import Any


def percentile_rank(history: list[float], current: float | None) -> float | None:
    """历史序列中 current 的百分位 (0=最便宜, 100=最贵)。"""
    if current is None or current <= 0:
        return None
    clean = sorted(x for x in history if x is not None and x > 0)
    if len(clean) < 20:
        return None
    below = sum(1 for x in clean if x < current)
    equal = sum(1 for x in clean if x == current)
    return round((below + 0.5 * equal) / len(clean) * 100, 1)


def extract_metric_series(records: list[dict[str, Any]], *keys: str) -> list[float]:
    series: list[float] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        for k in keys:
            if k not in row:
                continue
            try:
                v = float(row[k])
            except (TypeError, ValueError):
                continue
            if v > 0:
                series.append(v)
                break
    return series


def build_valuation_percentiles(
    valuation_history: list[dict[str, Any]],
    latest: dict[str, Any] | None = None,
    *,
    min_samples: int = 20,
) -> dict[str, Any]:
    """从 daily_basic 历史计算 PE/PB 分位，并附带股息率。"""
    latest = latest or {}
    pe_hist = extract_metric_series(valuation_history, "pe", "pe_ttm")
    pb_hist = extract_metric_series(valuation_history, "pb")
    dv_hist = extract_metric_series(valuation_history, "dv_ratio", "dv_ttm")
    pe_now = _f(latest.get("pe") or latest.get("pe_ttm"))
    pb_now = _f(latest.get("pb"))
    dv_now = _f(latest.get("dv_ratio") or latest.get("dv_ttm"))

    pe_pct = percentile_rank(pe_hist, pe_now)
    pb_pct = percentile_rank(pb_hist, pb_now)
    # 股息率：越高越好 → 用「高分位=更慷慨」单独标注，不参与 cheap/expensive 标签
    dv_pct = percentile_rank(dv_hist, dv_now)
    sample_days = max(len(pe_hist), len(pb_hist))

    out: dict[str, Any] = {
        "pe": pe_now,
        "pb": pb_now,
        "dv_ratio": dv_now,
        "pe_percentile": pe_pct,
        "pb_percentile": pb_pct,
        "dv_percentile": dv_pct,
        "sample_days": sample_days,
        "window": "3y_daily_basic",
        "ok": False,
    }
    if pe_pct is not None or pb_pct is not None:
        if sample_days >= min_samples:
            out["ok"] = True
            out["label"] = _label_from_percentile(pe_pct, pb_pct)
    return out


def valuation_score_from_percentiles(pe_pct: float | None, pb_pct: float | None) -> float | None:
    """分位越低（越便宜）分数越高。"""
    scores: list[float] = []
    for pct in (pe_pct, pb_pct):
        if pct is None:
            continue
        if pct <= 15:
            scores.append(90.0)
        elif pct <= 30:
            scores.append(78.0)
        elif pct <= 50:
            scores.append(62.0)
        elif pct <= 70:
            scores.append(45.0)
        elif pct <= 85:
            scores.append(32.0)
        else:
            scores.append(20.0)
    if not scores:
        return None
    return sum(scores) / len(scores)


def dividend_score_from_yield(dv_ratio: float | None) -> float | None:
    """股息率打分（Tushare dv_ratio 一般为百分比，如 2.5=2.5%）。"""
    v = _f(dv_ratio)
    if v is None or v < 0:
        return None
    # 偶发小数形式（0.025）→ 归一到百分数
    if 0 < v < 0.2:
        v = v * 100.0
    if v >= 4.0:
        return 90.0
    if v >= 3.0:
        return 80.0
    if v >= 2.0:
        return 68.0
    if v >= 1.0:
        return 55.0
    if v >= 0.5:
        return 48.0
    return 42.0


def blend_valuation_with_dividend(
    base_score: float,
    dv_ratio: float | None,
    *,
    weight: float = 0.15,
) -> tuple[float, list[str]]:
    """把股息率柔和并入估值分；高股息+低估值额外加分。"""
    evidence: list[str] = []
    dv_score = dividend_score_from_yield(dv_ratio)
    if dv_score is None:
        return base_score, evidence
    v = _f(dv_ratio) or 0.0
    if 0 < v < 0.2:
        v = v * 100.0
    evidence.append(f"股息率={v:.2f}%")
    w = max(0.0, min(0.4, float(weight)))
    blended = (1.0 - w) * base_score + w * dv_score
    # 低估值区 + 高股息：额外小幅加分（中长线安全垫）
    if base_score >= 70 and v >= 3.0:
        blended = min(100.0, blended + 5.0)
        evidence.append("低估值+高股息安全垫")
    return blended, evidence


def _label_from_percentile(pe_pct: float | None, pb_pct: float | None) -> str:
    vals = [x for x in (pe_pct, pb_pct) if x is not None]
    if not vals:
        return "unknown"
    avg = sum(vals) / len(vals)
    if avg <= 25:
        return "cheap"
    if avg <= 45:
        return "below_median"
    if avg <= 55:
        return "fair"
    if avg <= 75:
        return "above_median"
    return "expensive"


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
