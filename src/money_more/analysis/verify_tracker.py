"""第五波 B1：验证窗口命中率台账（到期必评）。

读取历史 ``reports/digests/*.json`` 中的 ``recommendations``（含
``verify_in_days`` / ``verify_signals``），对「已到验证期」的建议用
后续价格判定 命中 / 未命中 / 待定，并回写/更新 ``reports/verify_ledger.json``。

判定口径（中长线，不用精确撮合）：
- buy/add/hold：验证期内最大涨幅触及 ``verify_signals`` 中的正向条件
  （无信号时退化为：验证期内最大涨幅 ≥ +5% 记命中，≤ -8% 记未命中，其余待定）
- watch：验证期内最大回撤 ≤ -8% 记「规避成功」，否则记「规避未果」

只读历史 + 现价，不改决策。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from money_more.data.fetcher import normalize_code


def _load_digests(digests_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(digests_dir.glob("????-??-??.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["_date"] = p.stem
            out.append(d)
        except Exception:
            continue
    return out


def _iter_verify_candidates(digests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """抽出所有带验证窗口的历史建议。"""
    rows: list[dict[str, Any]] = []
    for d in digests:
        run_date = str(d.get("run_date") or d.get("_date") or "")
        for r in d.get("recommendations") or []:
            days = r.get("verify_in_days")
            if days is None:
                continue
            try:
                days_i = int(days)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "run_date": run_date,
                    "code": normalize_code(str(r.get("code") or "")),
                    "action": str(r.get("action") or "watch"),
                    "verify_in_days": days_i,
                    "verify_signals": list(r.get("verify_signals") or [])[:3],
                    "confidence": r.get("confidence"),
                    "sector": (r.get("sector_link") or {}).get("sector")
                    or r.get("sector_tag"),
                }
            )
    return rows


def _price_path(fetcher: Any, code: str, start: str, end: str) -> list[float]:
    """取 start..end 区间收盘价序列（东财前复权）。失败返回 []。"""
    try:
        hist = fetcher._fetch_daily_hist(  # noqa: SLF001
            code, start.replace("-", ""), end.replace("-", "")
        )
        if hist is None or hist.empty:
            return []
        col = next((c for c in ("收盘", "close") if c in hist.columns), None)
        if not col:
            return []
        return [float(x) for x in hist[col].tolist() if x == x]
    except Exception:
        return []


def evaluate_verify_window(
    row: dict[str, Any],
    prices: list[float],
    as_of: date,
) -> dict[str, Any]:
    """给定价格序列判定单条验证结果。"""
    out = dict(row)
    try:
        run_d = datetime.strptime(str(row.get("run_date")), "%Y-%m-%d").date()
    except ValueError:
        out["verdict"] = "unknown"
        return out
    due = run_d + timedelta(days=int(row.get("verify_in_days") or 14))
    out["due_date"] = due.isoformat()
    if as_of < due:
        out["verdict"] = "pending"
        return out
    if not prices:
        out["verdict"] = "no_price"
        return out
    base = prices[0]
    if not base or base <= 0:
        out["verdict"] = "no_price"
        return out
    peak = max(prices)
    trough = min(prices)
    up_pct = (peak - base) / base * 100
    down_pct = (trough - base) / base * 100
    out["max_up_pct"] = round(up_pct, 2)
    out["max_down_pct"] = round(down_pct, 2)
    action = str(row.get("action") or "watch")
    if action in ("buy", "add", "hold"):
        if up_pct >= 5.0:
            out["verdict"] = "hit"
        elif down_pct <= -8.0:
            out["verdict"] = "miss"
        else:
            out["verdict"] = "flat"
    elif action == "watch":
        out["verdict"] = "avoided" if down_pct <= -8.0 else "avoid_failed"
    else:
        out["verdict"] = "flat"
    return out


def build_verify_ledger(
    *,
    digests_dir: Path,
    fetcher: Any,
    as_of: date | None = None,
    max_lookback_days: int = 120,
) -> dict[str, Any]:
    """汇总台账：到期建议的命中率。"""
    as_of = as_of or date.today()
    digests = _load_digests(digests_dir)
    candidates = _iter_verify_candidates(digests)
    evaluated: list[dict[str, Any]] = []
    cutoff = as_of - timedelta(days=max_lookback_days)
    for row in candidates:
        try:
            run_d = datetime.strptime(str(row.get("run_date")), "%Y-%m-%d").date()
        except ValueError:
            continue
        if run_d < cutoff:
            continue
        end = min(as_of, run_d + timedelta(days=int(row.get("verify_in_days") or 14)))
        prices = _price_path(fetcher, row["code"], run_d.isoformat(), end.isoformat())
        evaluated.append(evaluate_verify_window(row, prices, as_of))

    done = [r for r in evaluated if r.get("verdict") in ("hit", "miss", "avoided", "avoid_failed", "flat")]
    buy_like = [r for r in done if str(r.get("action")) in ("buy", "add", "hold")]
    watch_like = [r for r in done if str(r.get("action")) == "watch"]
    hit = sum(1 for r in buy_like if r.get("verdict") == "hit")
    miss = sum(1 for r in buy_like if r.get("verdict") == "miss")
    avoided = sum(1 for r in watch_like if r.get("verdict") == "avoided")
    avoid_failed = sum(1 for r in watch_like if r.get("verdict") == "avoid_failed")

    def _rate(n: int, d: int) -> float | None:
        return round(n / d * 100, 1) if d else None

    return {
        "as_of": as_of.isoformat(),
        "total_due": len(done),
        "pending": sum(1 for r in evaluated if r.get("verdict") == "pending"),
        "buy_like": {
            "count": len(buy_like),
            "hit": hit,
            "miss": miss,
            "flat": len(buy_like) - hit - miss,
            "hit_rate_pct": _rate(hit, hit + miss),
        },
        "watch_like": {
            "count": len(watch_like),
            "avoided": avoided,
            "avoid_failed": avoid_failed,
            "avoid_rate_pct": _rate(avoided, avoided + avoid_failed),
        },
        "rows": evaluated[-60:],
        "priors": build_verify_priors(evaluated),
    }


def build_verify_priors(
    rows: list[dict[str, Any]],
    *,
    min_sector_samples: int = 3,
    sector_hit_rate_floor_pct: float = 30.0,
    consecutive_miss_limit: int = 3,
) -> dict[str, Any]:
    """由验证台账生成下一轮先验：赛道低命中 / 连续 miss → 降置信或禁新开。"""
    forbid_sectors: list[str] = []
    haircut_sectors: dict[str, float] = {}
    notes: list[str] = []

    by_sector: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if str(r.get("action") or "") not in ("buy", "add", "hold"):
            continue
        if r.get("verdict") not in ("hit", "miss", "flat"):
            continue
        sec = str(r.get("sector") or "").strip() or "unknown"
        by_sector.setdefault(sec, []).append(r)

    for sec, items in by_sector.items():
        if sec == "unknown":
            continue
        decided = [x for x in items if x.get("verdict") in ("hit", "miss")]
        if len(decided) < min_sector_samples:
            continue
        hits = sum(1 for x in decided if x.get("verdict") == "hit")
        rate = hits / len(decided) * 100
        if rate < sector_hit_rate_floor_pct:
            forbid_sectors.append(sec)
            notes.append(f"赛道[{sec}]验证命中率{rate:.0f}%<{sector_hit_rate_floor_pct:.0f}% → 禁新开")

    # 按 run_date 排序看连续 miss（全市场）
    timed = sorted(
        [r for r in rows if r.get("verdict") == "miss" and str(r.get("action")) in ("buy", "add", "hold")],
        key=lambda x: str(x.get("run_date") or ""),
    )
    streak = 0
    last_dates: list[str] = []
    for r in timed[-consecutive_miss_limit:]:
        streak += 1
        last_dates.append(str(r.get("run_date") or ""))
    if streak >= consecutive_miss_limit and len(timed) >= consecutive_miss_limit:
        # 最近 N 条到期 buy-like 均为 miss
        recent = [
            r
            for r in sorted(rows, key=lambda x: str(x.get("run_date") or ""))
            if r.get("verdict") in ("hit", "miss") and str(r.get("action")) in ("buy", "add", "hold")
        ][-consecutive_miss_limit:]
        if recent and all(r.get("verdict") == "miss" for r in recent):
            haircut_sectors["*"] = 0.75
            notes.append(f"连续{consecutive_miss_limit}次 buy-like miss → 全局置信×0.75")

    return {
        "forbid_sectors": forbid_sectors,
        "confidence_mult": haircut_sectors.get("*", 1.0),
        "notes": notes[:6],
    }
