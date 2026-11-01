"""复盘后处理：闸门已覆盖的旧案标已吸收；近重复经验只留一条。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# 这些说法对应已经在跑的闸门。再写成「做错」只会每轮重复审判旧案。
_ABSORBED = (
    re.compile(r"8\s*[-/月]\s*5|08[-/]05"),
    re.compile(r"微观结构|禁新开仓|禁开仓|liquidity_stress"),
    re.compile(r"经营现金流|OCF|现金流背离|现金流弱"),
    re.compile(r"纸面仓|paper_hold|模拟盘"),
)

_FOLD_RATIO = 0.72


def _norm(text: str) -> str:
    return re.sub(r"[\s，。；、,.;:：!！?？\"'“”‘’（）()]+", "", str(text or "")).lower()


def split_absorbed(items: list[Any] | None) -> tuple[list[str], list[str]]:
    """返回 (仍算做错, 已吸收)。"""
    fresh: list[str] = []
    absorbed: list[str] = []
    for raw in items or []:
        text = str(raw or "").strip()
        if not text:
            continue
        if any(p.search(text) for p in _ABSORBED):
            absorbed.append(text)
        else:
            fresh.append(text)
    return fresh, absorbed


def mark_absorbed_failures(rows: list[dict[str, Any]]) -> None:
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        fresh, absorbed = split_absorbed(row.get("what_failed"))
        row["what_failed"] = fresh
        if absorbed:
            row["absorbed"] = absorbed


def fold_lessons(*groups: list[dict[str, Any]]) -> list[str]:
    """近重复经验只保留先出现的一条。返回入库用的代表句。"""
    kept: list[str] = []
    kept_norm: list[str] = []
    for group in groups:
        for row in group or []:
            if not isinstance(row, dict):
                continue
            text = str(row.get("lesson") or "").strip()
            if not text:
                row["lesson_folded"] = False
                continue
            norm = _norm(text)
            match = None
            for prev in kept_norm:
                if norm == prev or SequenceMatcher(None, norm, prev).ratio() >= _FOLD_RATIO:
                    match = prev
                    break
            if match is None:
                kept.append(text)
                kept_norm.append(norm)
                row["lesson_folded"] = False
            else:
                row["lesson"] = ""
                row["lesson_folded"] = True
                row["lesson_folded_into"] = kept[kept_norm.index(match)]
    return kept


def watches_without_checkable_signals(
    reviews: list[dict[str, Any]],
    pending: list[dict[str, Any]],
) -> None:
    """观察票一条可机查信号都没有 → 标材料不足，不继续当成有效跟踪。"""
    from money_more.analysis.signal_checks import parse_verify_signal

    by_id = {int(p["id"]): p for p in pending or [] if p.get("id") is not None}
    for row in reviews or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("original_action") or row.get("action") or "")
        item = by_id.get(int(row.get("recommendation_id") or 0)) or {}
        if not action:
            action = str(item.get("action") or "")
        if action != "watch":
            continue
        signals = list(item.get("verify_signals") or [])
        if signals and any(parse_verify_signal(s) for s in signals):
            continue
        if not signals or not any(parse_verify_signal(s) for s in signals):
            row["material_gap"] = "无可机查验证信号"
            if str(row.get("status") or "") in ("", "tracking"):
                row["status"] = "pending"
                row["outcome"] = "pending"


def sanitize_window_note(note: str | None, anchor: str) -> str:
    """和本轮视图冲突的「无当日分析」不采用。"""
    text = str(note or "").strip()
    if not anchor:
        return text
    if re.search(r"无新增分析|无当日分析|当日无", text):
        return anchor
    return text
