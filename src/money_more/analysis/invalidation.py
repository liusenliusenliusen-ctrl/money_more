"""可机检的失效条件：把自然语言 invalidation 映射为规则检查。"""

from __future__ import annotations

import re
from typing import Any


def evaluate_invalidation(
    invalidation: str | list[str] | None,
    snap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回 fired / reasons。无法解析的条件记为 unchecked。"""
    texts: list[str] = []
    if isinstance(invalidation, list):
        texts = [str(x) for x in invalidation if x]
    elif invalidation:
        texts = [str(invalidation)]

    snap = snap or {}
    hist = snap.get("history") or {}
    quote = snap.get("quote") or {}
    close = _f(hist.get("close") or quote.get("最新价"))
    ma20 = _f(hist.get("ma20"))
    ma5 = _f(hist.get("ma5"))
    above = hist.get("above_ma20")
    chg = _f(hist.get("change_pct") or quote.get("涨跌幅"))

    fired: list[str] = []
    unchecked: list[str] = []

    for text in texts:
        t = text.lower().replace(" ", "")
        matched = False
        # close < MA20 / 跌破均线
        if any(k in t for k in ("跌破ma20", "close<ma20", "收盘<ma20", "破ma20", "跌破20日")):
            matched = True
            if above is False or (close and ma20 and close < ma20):
                fired.append(text)
        elif any(k in t for k in ("跌破ma5", "close<ma5", "破ma5")):
            matched = True
            if close and ma5 and close < ma5:
                fired.append(text)
        # 单日大跌
        m = re.search(r"(?:跌幅|下跌|跌超|跌过)\s*(\d+(?:\.\d+)?)\s*%?", text)
        if m:
            matched = True
            thr = float(m.group(1))
            if chg is not None and chg <= -thr:
                fired.append(text)
        # 涨停后回落等复杂条件 → unchecked
        if not matched:
            unchecked.append(text)

    return {
        "fired": fired,
        "unchecked": unchecked,
        "invalidated": bool(fired),
    }


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
