"""简易因子 IC：用历史评分卡与前瞻收益做相关性（watchlist 级）。"""

from __future__ import annotations

import json
import math
from typing import Any


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    deny = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denx == 0 or deny == 0:
        return None
    return round(num / (denx * deny), 4)


def compute_factor_ic_from_db(db: Any, lookback_runs: int = 20, forward_days: int = 5) -> dict[str, Any]:
    """
    从 stock_snapshots 提取 factor_scorecard，用后续收盘近似前瞻收益。
    样本少时仅返回诊断，不自动改权重。
    """
    with db.session() as conn:
        rows = conn.execute(
            """
            SELECT d.run_date, s.stock_code, s.analysis_json, s.snapshot_json
            FROM stock_snapshots s
            JOIN daily_runs d ON d.id = s.run_id
            WHERE d.status = 'success'
            ORDER BY d.run_date DESC
            LIMIT ?
            """,
            (lookback_runs * 20,),
        ).fetchall()

    # group by date
    by_date: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        a = json.loads(r["analysis_json"] or "{}")
        snap = json.loads(r["snapshot_json"] or "{}")
        sc = a.get("factor_scorecard") or {}
        scores = sc.get("scores") or {}
        close = (snap.get("history") or {}).get("close")
        by_date.setdefault(r["run_date"], []).append(
            {
                "code": r["stock_code"],
                "scores": scores,
                "total": sc.get("total_score"),
                "close": close,
            }
        )

    dates = sorted(by_date.keys())
    if len(dates) < 2:
        return {"ok": False, "reason": "历史成功运行不足", "dates": dates}

    # map code -> date -> close for forward return
    close_map: dict[str, dict[str, float]] = {}
    for d, items in by_date.items():
        for it in items:
            if it.get("close") is None:
                continue
            close_map.setdefault(it["code"], {})[d] = float(it["close"])

    factor_names = ["valuation", "momentum", "fund_flow", "sentiment", "quality", "narrative", "total"]
    pairs: dict[str, list[tuple[float, float]]] = {k: [] for k in factor_names}

    for i, d in enumerate(dates):
        # find a later date ~ forward_days ahead in the series (not calendar exact)
        fwd_idx = min(i + 1, len(dates) - 1)
        # try to skip ahead a few sessions if available
        for j in range(i + 1, min(i + forward_days + 1, len(dates))):
            fwd_idx = j
        if fwd_idx <= i:
            continue
        d2 = dates[fwd_idx]
        for it in by_date[d]:
            code = it["code"]
            c0 = close_map.get(code, {}).get(d)
            c1 = close_map.get(code, {}).get(d2)
            if c0 is None or c1 is None or c0 == 0:
                continue
            ret = (c1 - c0) / c0 * 100
            scores = it.get("scores") or {}
            for fname in factor_names:
                if fname == "total":
                    val = it.get("total")
                else:
                    val = scores.get(fname)
                if val is None:
                    continue
                try:
                    pairs[fname].append((float(val), float(ret)))
                except (TypeError, ValueError):
                    continue

    ics: dict[str, Any] = {}
    for fname, pts in pairs.items():
        if len(pts) < 5:
            ics[fname] = {"ic": None, "n": len(pts)}
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ics[fname] = {"ic": pearson(xs, ys), "n": len(pts)}

    # suggest downweight if IC strongly negative with enough samples
    suggestions: list[str] = []
    for fname, info in ics.items():
        if fname == "total":
            continue
        ic = info.get("ic")
        n = info.get("n") or 0
        if ic is not None and n >= 10 and ic < -0.05:
            suggestions.append(f"{fname} IC={ic} (n={n}) 偏负，建议临时降权")

    return {
        "ok": True,
        "forward_proxy": "next_available_run_close",
        "dates_used": len(dates),
        "ics": ics,
        "suggestions": suggestions,
    }
