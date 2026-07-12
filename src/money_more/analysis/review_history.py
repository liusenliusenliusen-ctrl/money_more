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
                    "market_phase_label": raw.get("market_phase_label"),
                    "market_style": raw.get("market_style"),
                    "market_style_label": raw.get("market_style_label"),
                    "risk_level": raw.get("risk_level"),
                    "primary_driver": raw.get("primary_driver"),
                    "headline_themes": raw.get("headline_themes") or [],
                    "sectors": [
                        {
                            "sector": s.get("sector"),
                            "priority": s.get("priority"),
                            "prosperity": s.get("prosperity"),
                            "valuation": s.get("valuation"),
                        }
                        for s in (raw.get("sectors") or [])[:8]
                        if isinstance(s, dict)
                    ],
                    "data_quality_score": raw.get("data_quality_score"),
                    "recommendations": [
                        {
                            "code": r.get("code"),
                            "action": r.get("action"),
                            "confidence": r.get("confidence"),
                            "factor_total": r.get("factor_total"),
                            "sector_tag": r.get("sector_tag"),
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


def build_prior_dimension_forecasts(
    reports_dir: Path,
    *,
    as_of: date,
    lookback_days: int = 120,
    min_age_days: int = 14,
    max_items: int = 8,
) -> list[dict[str, Any]]:
    """提取「已满观察期」的历史维度预测，供复盘对照今日现实。

    优先用 digests（含市场/板块/叙事）；旧 digest 缺字段时用报告压缩摘要兜底。
    """
    reports_dir = Path(reports_dir)
    digests_dir = reports_dir / "digests"
    cutoff = as_of - timedelta(days=max(1, lookback_days))
    min_date = as_of - timedelta(days=max(1, min_age_days))

    items: list[tuple[date, dict[str, Any]]] = []
    if digests_dir.exists():
        for p in digests_dir.glob("????-??-??.json"):
            d = _parse_ymd(p.name)
            if d is None or d < cutoff or d > min_date:
                continue
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            items.append(
                (
                    d,
                    {
                        "date": d.isoformat(),
                        "source": "decision_digest",
                        "market": {
                            "phase": raw.get("market_phase"),
                            "phase_label": raw.get("market_phase_label"),
                            "style": raw.get("market_style"),
                            "style_label": raw.get("market_style_label"),
                            "risk_level": raw.get("risk_level"),
                            "primary_driver": raw.get("primary_driver"),
                            "sector_allocation_hint": raw.get("sector_allocation_hint"),
                            "invalidation": raw.get("invalidation") or [],
                            "contradictions": raw.get("contradictions") or [],
                        },
                        "narratives": {
                            "headline_themes": raw.get("headline_themes") or [],
                            "market_narratives": raw.get("market_narratives") or [],
                            "risk_flags": raw.get("risk_flags") or [],
                        },
                        "sectors": raw.get("sectors") or [],
                        "recommendations": (raw.get("recommendations") or [])[:6],
                    },
                )
            )

    # 无 digest 时用 md 摘要补几条骨架
    if len(items) < 3 and reports_dir.exists():
        have = {d for d, _ in items}
        for p in sorted(reports_dir.glob("????-??-??.md")):
            d = _parse_ymd(p.name)
            if d is None or d < cutoff or d > min_date or d in have:
                continue
            try:
                compact = _compact_md_report(p.read_text(encoding="utf-8"))
            except OSError:
                continue
            items.append(
                (
                    d,
                    {
                        "date": d.isoformat(),
                        "source": "report_md",
                        "market": {"summary_excerpt": compact.get("summary")},
                        "narratives": {"excerpt": compact.get("excerpt")},
                        "sectors": [],
                        "recommendations": [],
                        "action_lines": compact.get("action_lines") or [],
                    },
                )
            )

    items.sort(key=lambda x: x[0])
    if len(items) > max_items:
        step = max(1, len(items) // max_items)
        sampled = items[::step][: max_items - 1]
        if items[-1] not in sampled:
            sampled.append(items[-1])
        items = sampled
    return [x[1] for x in items]


def compact_current_view(current_view: dict[str, Any] | None) -> dict[str, Any]:
    """把本轮分析压缩成复盘对照用的「当前现实」。"""
    if not current_view:
        return {}
    market = current_view.get("market") or {}
    digest = current_view.get("intelligence_digest") or {}
    sectors = []
    for sec in current_view.get("sectors") or []:
        a = sec.get("analysis") or sec
        sectors.append(
            {
                "sector": a.get("sector") or sec.get("sector"),
                "priority": a.get("priority"),
                "prosperity": a.get("prosperity"),
                "valuation": a.get("valuation"),
                "policy_wind": a.get("policy_wind"),
                "crowding_risk": (a.get("sentiment") or {}).get("crowding_risk"),
                "summary": (a.get("summary") or "")[:160],
            }
        )
    return {
        "market": {
            "phase": market.get("phase"),
            "phase_label": market.get("phase_label"),
            "style": market.get("style"),
            "style_label": market.get("style_label"),
            "risk_level": market.get("risk_level"),
            "primary_driver": market.get("primary_driver"),
            "sector_allocation_hint": market.get("sector_allocation_hint"),
            "summary": (market.get("summary") or "")[:240],
        },
        "narratives": {
            "headline_themes": list(digest.get("headline_themes") or [])[:5],
            "market_narratives": list(digest.get("market_narratives") or [])[:4],
            "risk_flags": list(digest.get("risk_flags") or [])[:4],
        },
        "sectors": sectors,
        "recommendation_actions": [
            {
                "code": r.get("code"),
                "action": r.get("action"),
                "sector_tag": r.get("sector_tag"),
                "confidence": r.get("confidence"),
            }
            for r in (current_view.get("recommendations") or [])[:10]
            if isinstance(r, dict)
        ],
    }
