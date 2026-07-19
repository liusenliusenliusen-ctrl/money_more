"""复盘用历史报告语料：近 60 日报告/digest 全漏斗快照。"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
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
    summary = ""
    for i, ln in enumerate(lines):
        if "情报综述" in ln or ln.startswith("## 0"):
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


def _digest_to_item(d: date, raw: dict[str, Any]) -> dict[str, Any]:
    recs = raw.get("recommendations") or []
    return {
        "date": d.isoformat(),
        "market_phase": raw.get("market_phase"),
        "market_phase_label": raw.get("market_phase_label"),
        "market_style": raw.get("market_style"),
        "market_style_label": raw.get("market_style_label"),
        "risk_level": raw.get("risk_level"),
        "primary_driver": raw.get("primary_driver"),
        "sector_allocation_hint": raw.get("sector_allocation_hint"),
        "invalidation": list(raw.get("invalidation") or [])[:4],
        "contradictions": list(raw.get("contradictions") or [])[:4],
        "headline_themes": raw.get("headline_themes") or [],
        "market_narratives": raw.get("market_narratives") or [],
        "risk_flags": raw.get("risk_flags") or [],
        "sectors": [
            {
                "sector": s.get("sector"),
                "priority": s.get("priority"),
                "prosperity": s.get("prosperity"),
                "valuation": s.get("valuation"),
                "policy_wind": s.get("policy_wind"),
                "worth_research": s.get("worth_research"),
            }
            for s in (raw.get("sectors") or [])[:10]
            if isinstance(s, dict)
        ],
        "data_quality_score": raw.get("data_quality_score"),
        "recommendations": [
            {
                "code": r.get("code"),
                "action": r.get("action"),
                "confidence": r.get("confidence"),
                "position_pct": r.get("position_pct"),
                "factor_total": r.get("factor_total"),
                "sector_tag": r.get("sector_tag"),
                "invalidation": r.get("invalidation"),
            }
            for r in recs[:12]
            if isinstance(r, dict)
        ],
    }


def load_historical_reports_corpus(
    reports_dir: Path,
    *,
    as_of: date,
    lookback_days: int = 60,
    max_reports: int = 40,
) -> dict[str, Any]:
    """汇总 lookback 内 markdown + decision digests。

    结构化 digest 尽量全量纳入（默认 60 日内不抽样丢中间期）；
    markdown 过长时再均匀抽样。
    """
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
        # digest 结构化且小：窗口内尽量全要；极端多时才抽样
        hard_cap = max(max_reports, 60)
        if len(dig_cands) > hard_cap:
            step = max(1, len(dig_cands) // hard_cap)
            dig_cands = dig_cands[::step][:hard_cap]
            if dig_cands and dig_cands[-1][0] != dig_cands[-1][0]:
                pass
        for d, p in dig_cands:
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(raw, dict):
                digest_items.append(_digest_to_item(d, raw))

    actual_span_days = None
    dates = [date.fromisoformat(x["date"]) for x in digest_items if x.get("date")]
    dates += [date.fromisoformat(x["date"]) for x in md_items if x.get("date")]
    if dates:
        actual_span_days = (max(dates) - min(dates)).days

    return {
        "window": {
            "as_of": as_of.isoformat(),
            "lookback_days": lookback_days,
            "cutoff": cutoff.isoformat(),
            "actual_span_days": actual_span_days,
            "note": (
                f"取材窗口近 {lookback_days} 日"
                + (
                    f"（实际有材料约 {actual_span_days} 日）"
                    if actual_span_days is not None and actual_span_days < lookback_days
                    else ""
                )
            ),
        },
        "report_count": len(md_items),
        "digest_count": len(digest_items),
        "reports": md_items,
        "decision_digests": digest_items,
        "note": "窗口内结构化 digest 优先全量；复盘对照 current_view，勿用单点涨跌结案",
    }


def build_action_lifecycles(digest_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按代码串联窗口内动作，供复盘「哪次推荐 / 是否改口」。"""
    by_code: dict[str, list[dict[str, Any]]] = {}
    for dig in digest_items:
        d = dig.get("date")
        for r in dig.get("recommendations") or []:
            if not isinstance(r, dict):
                continue
            code = "".join(ch for ch in str(r.get("code") or "") if ch.isdigit())[-6:]
            if not code:
                continue
            code = code.zfill(6)
            by_code.setdefault(code, []).append(
                {
                    "date": d,
                    "action": r.get("action"),
                    "confidence": r.get("confidence"),
                    "position_pct": r.get("position_pct"),
                    "sector_tag": r.get("sector_tag"),
                    "invalidation": r.get("invalidation"),
                }
            )
    out: list[dict[str, Any]] = []
    for code, chain in sorted(by_code.items()):
        chain = sorted(chain, key=lambda x: str(x.get("date") or ""))
        actions = [str(x.get("action") or "") for x in chain]
        out.append(
            {
                "code": code,
                "first_date": chain[0].get("date"),
                "last_date": chain[-1].get("date"),
                "n_updates": len(chain),
                "action_path": actions,
                "changed": len(set(a.lower() for a in actions if a)) > 1,
                "chain": chain,
            }
        )
    return out


def load_db_market_history(db: Any, lookback_days: int = 60, limit: int = 40) -> dict[str, Any]:
    """从 DB 拉市场相位序列。"""
    n = min(limit, max(8, lookback_days // 3))
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
    lookback_days: int = 60,
    min_age_days: int = 14,
    max_items: int = 24,
) -> list[dict[str, Any]]:
    """提取窗口内历史维度预测；优先已满观察期，不够再放宽纳入较新材料（标 young）。"""
    reports_dir = Path(reports_dir)
    digests_dir = reports_dir / "digests"
    cutoff = as_of - timedelta(days=max(1, lookback_days))
    min_date = as_of - timedelta(days=max(1, min_age_days))

    matured: list[tuple[date, dict[str, Any]]] = []
    young: list[tuple[date, dict[str, Any]]] = []

    if digests_dir.exists():
        for p in digests_dir.glob("????-??-??.json"):
            d = _parse_ymd(p.name)
            if d is None or d < cutoff or d > as_of:
                continue
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            item = {
                "date": d.isoformat(),
                "source": "decision_digest",
                "matured": d <= min_date,
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
                "recommendations": (raw.get("recommendations") or [])[:8],
            }
            if d <= min_date:
                matured.append((d, item))
            else:
                young.append((d, item))

    if len(matured) + len(young) < 3 and reports_dir.exists():
        have = {d for d, _ in matured} | {d for d, _ in young}
        for p in sorted(reports_dir.glob("????-??-??.md")):
            d = _parse_ymd(p.name)
            if d is None or d < cutoff or d > as_of or d in have:
                continue
            try:
                compact = _compact_md_report(p.read_text(encoding="utf-8"))
            except OSError:
                continue
            item = {
                "date": d.isoformat(),
                "source": "report_md",
                "matured": d <= min_date,
                "market": {"summary_excerpt": compact.get("summary")},
                "narratives": {"excerpt": compact.get("excerpt")},
                "sectors": [],
                "recommendations": [],
                "action_lines": compact.get("action_lines") or [],
            }
            (matured if d <= min_date else young).append((d, item))

    matured.sort(key=lambda x: x[0])
    young.sort(key=lambda x: x[0])
    # 优先全要 matured；再补 young；超 cap 时对 matured 均匀抽样但保留首尾
    items = matured
    if len(items) > max_items:
        step = max(1, len(items) // max_items)
        sampled = items[::step][: max_items - 1]
        if items[-1] not in sampled:
            sampled.append(items[-1])
        items = sampled
    room = max(0, max_items - len(items))
    if room and young:
        items = items + young[-room:]
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
            "invalidation": list(market.get("invalidation") or [])[:4],
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
                "invalidation": r.get("invalidation"),
            }
            for r in (current_view.get("recommendations") or [])[:10]
            if isinstance(r, dict)
        ],
    }
