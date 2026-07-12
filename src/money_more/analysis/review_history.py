"""复盘用历史报告语料：近几个月的报告/digest/市场相位压缩摘要。"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


def _parse_ymd(name: str) -> date | None:
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", name)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _compact_md_report(text: str, max_chars: int = 500) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    dq = next((ln for ln in lines if ln.startswith("**数据质量**")), None)
    # 情报综述第一段 / 市场阶段
    summary = ""
    for i, ln in enumerate(lines):
        if "情报综述" in ln or ln.startswith("## 0"):
            # 下一非空非标题行
            for j in range(i + 1, min(i + 8, len(lines))):
                if lines[j].startswith("#") or lines[j].startswith("**"):
                    continue
                summary = lines[j][:350]
                break
            break
    if not summary:
        for ln in lines:
            if ln.startswith("## 1") or "市场阶段" in ln:
                continue
            if len(ln) > 40 and not ln.startswith("#") and not ln.startswith("|"):
                summary = ln[:350]
                break
    # 荐股动作粗提取
    actions: list[str] = []
    for ln in lines:
        if re.search(r"\b(buy|add|hold|sell|reduce|watch)\b", ln, re.I) and re.search(r"\d{6}", ln):
            actions.append(ln[:120])
        if len(actions) >= 6:
            break
    blob = " | ".join(x for x in [dq, summary] if x)
    return {
        "data_quality_line": dq,
        "summary": summary,
        "action_lines": actions,
        "excerpt": blob[:max_chars],
    }


def load_historical_reports_corpus(
    reports_dir: Path,
    *,
    as_of: date,
    lookback_days: int = 120,
    max_reports: int = 24,
) -> dict[str, Any]:
    """汇总 lookback 内的 markdown 报告 + decision digests，供复盘对照历史经验。"""
    cutoff = as_of - timedelta(days=max(1, lookback_days))
    reports_dir = Path(reports_dir)
    digests_dir = reports_dir / "digests"

    md_items: list[dict[str, Any]] = []
    if reports_dir.exists():
        candidates: list[tuple[date, Path]] = []
        for p in reports_dir.glob("????-??-??.md"):
            d = _parse_ymd(p.name)
            if d is None or d < cutoff or d > as_of:
                continue
            candidates.append((d, p))
        candidates.sort(key=lambda x: x[0])
        # 均匀抽样：过多时取最早/中段/最近，避免只看最近几周
        if len(candidates) > max_reports:
            step = max(1, len(candidates) // max_reports)
            sampled = candidates[::step][: max_reports - 1]
            if candidates[-1] not in sampled:
                sampled.append(candidates[-1])
            candidates = sampled
        for d, p in candidates:
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            compact = _compact_md_report(text)
            md_items.append({"date": d.isoformat(), "file": p.name, **compact})

    digest_items: list[dict[str, Any]] = []
    if digests_dir.exists():
        dig_cands: list[tuple[date, Path]] = []
        for p in digests_dir.glob("????-??-??.json"):
            d = _parse_ymd(p.name)
            if d is None or d < cutoff or d > as_of:
                continue
            dig_cands.append((d, p))
        dig_cands.sort(key=lambda x: x[0])
        if len(dig_cands) > max_reports:
            step = max(1, len(dig_cands) // max_reports)
            dig_cands = dig_cands[::step][:max_reports]
        for d, p in dig_cands:
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            recs = raw.get("recommendations") or []
            digest_items.append(
                {
                    "date": d.isoformat(),
                    "market_phase": raw.get("market_phase"),
                    "market_style": raw.get("market_style"),
                    "risk_level": raw.get("risk_level"),
                    "data_quality_score": raw.get("data_quality_score"),
                    "recommendations": [
                        {
                            "code": r.get("code"),
                            "action": r.get("action"),
                            "confidence": r.get("confidence"),
                            "factor_total": r.get("factor_total"),
                        }
                        for r in recs[:8]
                        if isinstance(r, dict)
                    ],
                }
            )

    return {
        "window": {
            "as_of": as_of.isoformat(),
            "lookback_days": lookback_days,
            "cutoff": cutoff.isoformat(),
        },
        "report_count": len(md_items),
        "digest_count": len(digest_items),
        "reports": md_items,
        "decision_digests": digest_items,
        "note": "近几个月报告的压缩摘要；复盘时结合单条 original_context 与本语料提炼跨期经验",
    }


def load_db_market_history(db: Any, lookback_days: int = 120, limit: int = 30) -> dict[str, Any]:
    """从 DB 拉更长的市场相位序列（补充 markdown 缺失时的骨架）。"""
    # get_prior_context 只有最近 N 条；这里按 lookback 放大
    n = min(limit, max(8, lookback_days // 5))
    ctx = db.get_prior_context(limit=n)
    return {
        "market_history": ctx.get("market_history") or [],
        "days": ctx.get("days") or 0,
        "source": "db_market_snapshots",
    }
