"""复盘后处理：闸门已覆盖的旧案标已吸收；近重复经验只留一条。"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
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


def _clean_signals(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def load_digest_verify_signals(digests_dir: Path | None) -> dict[tuple[str, str], list[str]]:
    """旧建议的 extra_json 没存验证信号时，从当日摘要按日期+代码回填。"""
    out: dict[tuple[str, str], list[str]] = {}
    root = Path(digests_dir) if digests_dir else None
    if root is None or not root.exists():
        return out
    for path in root.glob("????-??-??.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        day = str(raw.get("run_date") or path.stem)[:10]
        for rec in raw.get("recommendations") or []:
            if not isinstance(rec, dict):
                continue
            digits = "".join(ch for ch in str(rec.get("code") or "") if ch.isdigit())
            code = digits[-6:].zfill(6) if digits else ""
            signals = _clean_signals(rec.get("verify_signals"))
            if code and signals:
                out[(day, code)] = signals
    return out


def resolve_verify_signals(
    stored: Any,
    run_date: Any,
    code: Any,
    digest_index: dict[tuple[str, str], list[str]] | None,
) -> list[str]:
    """库里已有信号就用库里的；否则用当日摘要。"""
    signals = _clean_signals(stored)
    if signals:
        return signals
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    norm = digits[-6:].zfill(6) if digits else ""
    day = str(run_date or "")[:10]
    if not norm or not day:
        return []
    return list((digest_index or {}).get((day, norm)) or [])


def watches_without_checkable_signals(
    reviews: list[dict[str, Any]],
    pending: list[dict[str, Any]],
) -> None:
    """观察票完全没有验证信号 → 标材料不足。有信号但还不能机查的，留给叙事，不盖章。"""
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
        if _clean_signals(item.get("verify_signals")):
            continue
        row["material_gap"] = "未记录验证信号"
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
