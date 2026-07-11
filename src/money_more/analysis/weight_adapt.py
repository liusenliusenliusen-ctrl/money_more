"""将因子 IC 建议转为临时权重调整。"""

from __future__ import annotations

from typing import Any

from money_more.analysis.factor_scorecard import DEFAULT_WEIGHTS


def weights_from_ic(ic_report: dict[str, Any] | None) -> dict[str, float]:
    """IC 明显为负且样本足够时降权，其余保持默认。"""
    w = dict(DEFAULT_WEIGHTS)
    if not ic_report or not ic_report.get("ok"):
        return w
    ics = ic_report.get("ics") or {}
    for name in list(w.keys()):
        info = ics.get(name) or {}
        ic = info.get("ic")
        n = info.get("n") or 0
        if ic is None or n < 10:
            continue
        if ic < -0.1:
            w[name] *= 0.5
        elif ic < -0.05:
            w[name] *= 0.75
        elif ic > 0.1:
            w[name] *= 1.15
    # renormalize
    s = sum(w.values()) or 1.0
    return {k: round(v / s, 4) for k, v in w.items()}
