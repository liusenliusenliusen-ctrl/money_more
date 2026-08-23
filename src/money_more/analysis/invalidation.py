"""可机检的失效条件：把自然语言 invalidation 映射为规则检查。

中长线约定：MA5 / 单日涨跌幅阈值不作为机检开火（记 unchecked）；
MA20/MA60 在文本明确跌破对应均线时可机检。
"""

from __future__ import annotations

import re
from typing import Any


def evaluate_invalidation(
    invalidation: str | list[str] | None,
    snap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回 fired / unchecked。短线条件永不 fired。"""
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
    ma60 = _f(hist.get("ma60"))
    above = hist.get("above_ma20")

    fired: list[str] = []
    unchecked: list[str] = []

    for text in texts:
        t = text.lower().replace(" ", "")
        matched = False

        # —— 短线：永不机检开火 ——
        if any(k in t for k in ("跌破ma5", "close<ma5", "破ma5", "跌破5日")):
            unchecked.append(text)
            continue
        # 单日/百分比跌幅阈值（旧短线机检）→ unchecked
        if re.search(r"(?:跌幅|下跌|跌超|跌过)\s*\d+(?:\.\d+)?\s*%?", text) and not any(
            k in text for k in ("连续两月", "连续两周", "同比", "环比", "营收", "净利", "利润", "PMI")
        ):
            if not any(k in t for k in ("跌破ma20", "跌破ma60", "跌破20日", "跌破60日")):
                unchecked.append(text)
                continue

        # —— 中期技术锚 ——
        if any(k in t for k in ("跌破ma20", "close<ma20", "收盘<ma20", "破ma20", "跌破20日", "收盘跌破ma20")):
            matched = True
            if above is False or (close is not None and ma20 is not None and close < ma20):
                fired.append(text)
        elif any(k in t for k in ("跌破ma60", "close<ma60", "破ma60", "跌破60日")):
            matched = True
            if close is not None and ma60 is not None and close < ma60:
                fired.append(text)

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
