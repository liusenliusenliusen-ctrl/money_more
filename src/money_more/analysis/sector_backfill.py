"""深度池定稿后：为池内有票、本轮尚无 B1 的细板块补跑板块分析。"""

from __future__ import annotations

from typing import Any

from money_more.analysis.sector_map import infer_sector, normalize_industry
from money_more.data.fetcher import normalize_code


def infer_deep_pool_sectors(
    deep_codes: list[str] | None,
    top_candidates: list[dict[str, Any]] | None = None,
) -> list[str]:
    """从深度池代码推断细板块短标签（去重，保持 deep_codes 顺序）。"""
    by_code: dict[str, str] = {}
    for row in top_candidates or []:
        if not isinstance(row, dict):
            continue
        code = normalize_code(str(row.get("code") or ""))
        sec = str(row.get("sector") or "").strip()
        if code and sec and sec.lower() not in ("unknown", "none", "nan", "其他"):
            by_code[code] = sec

    out: list[str] = []
    seen: set[str] = set()
    for raw in deep_codes or []:
        code = normalize_code(str(raw or ""))
        if not code:
            continue
        sec = by_code.get(code) or infer_sector(code)
        label = _canonical_sector_label(sec)
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def existing_sector_names(sector_analyses: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for sec in sector_analyses or []:
        if not isinstance(sec, dict):
            continue
        a = sec.get("analysis") if isinstance(sec.get("analysis"), dict) else {}
        name = str((a or {}).get("sector") or sec.get("sector") or "").strip()
        if name:
            names.append(name)
    return names


def sector_already_analyzed(name: str, existing: list[str] | None) -> bool:
    """模糊覆盖：精确 / 归一短名 / 互相子串。"""
    label = _canonical_sector_label(name)
    if not label:
        return True
    for raw in existing or []:
        other = str(raw or "").strip()
        if not other:
            continue
        other_n = _canonical_sector_label(other) or other
        if label == other or label == other_n:
            return True
        if label in other or other in label or label in other_n or other_n in label:
            return True
    return False


def sectors_needing_backfill(
    deep_sectors: list[str] | None,
    sector_analyses: list[dict[str, Any]] | None,
    *,
    max_backfill: int = 8,
) -> list[str]:
    """深度池主题中尚未被本轮 B1 覆盖的细板块（上限 max_backfill）。"""
    existing = existing_sector_names(sector_analyses)
    missing: list[str] = []
    for name in deep_sectors or []:
        label = _canonical_sector_label(name)
        if not label:
            continue
        if sector_already_analyzed(label, existing):
            continue
        if sector_already_analyzed(label, missing):
            continue
        missing.append(label)
        if max_backfill > 0 and len(missing) >= max_backfill:
            break
    return missing


def _canonical_sector_label(name: str | None) -> str | None:
    text = str(name or "").strip()
    if not text or text.lower() in ("unknown", "none", "nan", "其他"):
        return None
    return normalize_industry(text) or text
