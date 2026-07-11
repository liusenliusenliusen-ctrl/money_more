"""持仓相关性/同板块集中度的轻量风险检查（不依赖 LLM）。"""

from __future__ import annotations

from typing import Any

from money_more.analysis.sector_map import infer_sector


def risk_check_book(
    recommendations: list[dict[str, Any]],
    *,
    max_single: float = 20.0,
    max_total: float = 80.0,
    max_sector: float | None = None,
) -> dict[str, Any]:
    max_sector = max_sector if max_sector is not None else max_single * 1.5
    issues: list[str] = []
    deploy = [
        r
        for r in recommendations
        if str(r.get("action", "")).lower() in ("buy", "add", "hold")
        and float(r.get("position_pct") or 0) > 0
    ]
    total = sum(float(r.get("position_pct") or 0) for r in deploy)
    if total > max_total + 1e-6:
        issues.append(f"总仓位 {total:.1f}% > {max_total}%")

    by_sector: dict[str, float] = {}
    for r in deploy:
        code = str(r.get("code") or "")
        tag = str(r.get("sector_tag") or infer_sector(code) or "unknown")
        pct = float(r.get("position_pct") or 0)
        if pct > max_single + 1e-6:
            issues.append(f"{code} 单票 {pct:.1f}% > {max_single}%")
        by_sector[tag] = by_sector.get(tag, 0.0) + pct

    for tag, pct in by_sector.items():
        if tag != "unknown" and pct > max_sector + 1e-6:
            issues.append(f"板块[{tag}] {pct:.1f}% > {max_sector}%")

    return {
        "ok": not issues,
        "total_position_pct": round(total, 2),
        "by_sector": {k: round(v, 2) for k, v in by_sector.items()},
        "issues": issues,
    }
