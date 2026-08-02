"""将因子 IC 建议转为临时权重调整。"""

from __future__ import annotations

from typing import Any

from money_more.analysis.factor_scorecard import DEFAULT_WEIGHTS

# 中长线默认关闭：IC 的「未来」实为下一次成功跑完收盘，日频会拧向短线噪声
_MIDLONG_HORIZONS = frozenset({"medium_long", "mid_long", "中长线"})


def weights_from_ic(
    ic_report: dict[str, Any] | None,
    *,
    investment_horizon: str | None = None,
    allow_adapt: bool | None = None,
) -> dict[str, float]:
    """IC 明显为负且样本足够时降权，其余保持默认。

    medium_long 默认不改权（见 docs/data-semantics-guide.md S4）。
    显式 allow_adapt=True 时，中长线仅调整 quality/valuation 且要求更大样本。
    """
    w = dict(DEFAULT_WEIGHTS)
    horizon = str(investment_horizon or "").strip().lower()
    is_midlong = investment_horizon is None or horizon == "" or horizon in _MIDLONG_HORIZONS

    if allow_adapt is False:
        return w
    if allow_adapt is None and is_midlong:
        return w
    if not ic_report or not ic_report.get("ok"):
        return w

    touchable = set(w.keys())
    min_n = 10
    if is_midlong:
        touchable = {"quality", "valuation"}
        min_n = 20

    ics = ic_report.get("ics") or {}
    for name in list(w.keys()):
        if name not in touchable:
            continue
        info = ics.get(name) or {}
        ic = info.get("ic")
        n = info.get("n") or 0
        if ic is None or n < min_n:
            continue
        if ic < -0.1:
            w[name] *= 0.5
        elif ic < -0.05:
            w[name] *= 0.75
        elif ic > 0.1:
            w[name] *= 1.15
    s = sum(w.values()) or 1.0
    return {k: round(v / s, 4) for k, v in w.items()}
