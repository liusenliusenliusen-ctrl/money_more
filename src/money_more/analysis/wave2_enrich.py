"""第二波：sector_link / 验证窗口 / 缺标的 / 维度对照表（纯函数，供校验与报告）。"""

from __future__ import annotations

from typing import Any

from money_more.analysis.sector_map import infer_sector, theme_bucket
from money_more.data.fetcher import normalize_code


def enrich_sector_link(
    rec: dict[str, Any],
    *,
    sector_analyses: list[dict[str, Any]] | None = None,
    research_by_code: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """补全 sector_link；返回 (link, override_note|None)。"""
    code = normalize_code(str(rec.get("code") or ""))
    existing = rec.get("sector_link") if isinstance(rec.get("sector_link"), dict) else {}
    link = dict(existing or {})
    note: str | None = None

    sector = str(link.get("sector") or rec.get("sector_tag") or rec.get("sector") or "").strip()
    if not sector:
        sector = infer_sector(code) or ""
        if sector:
            note = f"{code}: sector_link.sector 由系统补全={sector}"

    meta = _sector_meta(sector, sector_analyses)
    if not link.get("sector") and sector:
        link["sector"] = sector
    link.setdefault("sector_priority", meta.get("priority") or "unknown")
    link.setdefault("sector_prosperity", meta.get("prosperity") or "unknown")

    research = (research_by_code or {}).get(code) or {}
    rating = str(
        link.get("from_research_rating")
        or research.get("research_rating")
        or research.get("rating")
        or ""
    ).lower()
    if rating:
        link["from_research_rating"] = rating

    action = str(rec.get("action") or "watch").lower()
    if not link.get("action_rationale_vs_research"):
        if rating and action and rating != action:
            link["action_rationale_vs_research"] = f"research {rating} → {action}"
        elif action:
            link["action_rationale_vs_research"] = f"action={action}"

    return link, note


def enrich_verify_window(rec: dict[str, Any], *, default_days: int = 14) -> tuple[dict[str, Any], str | None]:
    """补全 verify_in_days / verify_signals；返回更新字段与 override。"""
    note: str | None = None
    days = rec.get("verify_in_days")
    try:
        days_i = int(days) if days is not None else default_days
    except (TypeError, ValueError):
        days_i = default_days
        note = f"{rec.get('code')}: verify_in_days 非法 → {default_days}"
    if days is None:
        note = f"{rec.get('code')}: 补全 verify_in_days={default_days}"

    signals = rec.get("verify_signals")
    if not isinstance(signals, list) or not [str(x).strip() for x in signals if str(x).strip()]:
        inv = str(rec.get("invalidation") or "").strip()
        signals = [
            f"持有/观察满 {days_i} 日未触发失效条件"
            + (f"（参考：{inv[:40]}）" if inv else ""),
        ]
        note = (note + "；" if note else f"{rec.get('code')}: ") + "补全 verify_signals 默认"
    else:
        signals = [str(x).strip() for x in signals if str(x).strip()][:5]

    return {"verify_in_days": days_i, "verify_signals": signals}, note


def build_sector_coverage(
    sector_analyses: list[dict[str, Any]] | None,
    recommendations: list[dict[str, Any]] | None,
    *,
    deep_codes: list[str] | None = None,
    min_priority: str = "high",
) -> list[dict[str, Any]]:
    """板块优先级 vs 深度池/建议映射；缺标的显式列出。"""
    pri_rank = {"high": 3, "高": 3, "medium": 2, "中": 2, "low": 1, "低": 1}
    min_r = pri_rank.get(min_priority, 3)
    deep = {normalize_code(c) for c in (deep_codes or []) if c}

    # code -> sectors from recs
    rec_by_sector: dict[str, list[str]] = {}
    for r in recommendations or []:
        code = normalize_code(str(r.get("code") or ""))
        if not code:
            continue
        sl = r.get("sector_link") if isinstance(r.get("sector_link"), dict) else {}
        tag = str(sl.get("sector") or r.get("sector_tag") or r.get("sector") or infer_sector(code) or "")
        if tag:
            rec_by_sector.setdefault(tag, []).append(code)

    out: list[dict[str, Any]] = []
    for sec in sector_analyses or []:
        a = sec.get("analysis") or {}
        name = str(a.get("sector") or sec.get("sector") or "").strip()
        if not name:
            continue
        pri = str(a.get("priority") or "").lower()
        if pri_rank.get(pri, 0) < min_r:
            continue
        mapped_recs = list(rec_by_sector.get(name) or [])
        # 主题模糊匹配
        if not mapped_recs:
            for tag, codes in rec_by_sector.items():
                if name in tag or tag in name or theme_bucket(name) == theme_bucket(tag):
                    mapped_recs.extend(codes)
        mapped_recs = list(dict.fromkeys(mapped_recs))
        deep_hit = [c for c in mapped_recs if c in deep] if deep else mapped_recs
        # 若 deep 有票但未进建议：用 infer 扫 deep
        if not deep_hit and deep:
            for c in deep:
                s = infer_sector(c) or ""
                if s and (name in s or s in name or theme_bucket(name) == theme_bucket(s)):
                    deep_hit.append(c)
        gap = len(deep_hit) == 0
        out.append(
            {
                "sector": name,
                "priority": a.get("priority"),
                "prosperity": a.get("prosperity"),
                "deep_codes": deep_hit[:8],
                "rec_codes": mapped_recs[:8],
                "missing_target": gap,
                "note": (
                    f"{name} 优先级{a.get('priority')}，本轮深度池无映射标的 → 仅约束风格/仓位，非漏推个股"
                    if gap
                    else f"{name} 已映射 {len(deep_hit)} 只"
                ),
            }
        )
    return out


def build_dimension_diff_table(
    prior_forecasts: list[dict[str, Any]] | None,
    current_view: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """当时 vs 后来：phase/style/risk/板块优先级结构化 diff。"""
    priors = list(prior_forecasts or [])
    cur = current_view or {}
    cur_m = cur.get("market") or {}
    rows: list[dict[str, Any]] = []

    # 取窗口内最早与最近各一条做对照（若只有一条则 vs current）
    if not priors:
        rows.append(
            {
                "dimension": "market",
                "field": "phase",
                "then": None,
                "now": cur_m.get("phase") or cur_m.get("phase_label"),
                "verdict": "unknown",
                "note": "无历史维度快照",
            }
        )
        return rows

    oldest = priors[0]
    newest = priors[-1]
    then_m = oldest.get("market") or {}
    later_m = (newest.get("market") or {}) if newest is not oldest else cur_m
    if not later_m:
        later_m = cur_m

    for field, then_v, now_v in (
        ("phase", then_m.get("phase") or then_m.get("phase_label"), later_m.get("phase") or later_m.get("phase_label") or cur_m.get("phase")),
        ("style", then_m.get("style") or then_m.get("style_label"), later_m.get("style") or later_m.get("style_label") or cur_m.get("style")),
        ("risk_level", then_m.get("risk_level"), later_m.get("risk_level") or cur_m.get("risk_level")),
    ):
        rows.append(
            {
                "dimension": "market",
                "field": field,
                "then": then_v,
                "then_date": oldest.get("date"),
                "now": now_v,
                "now_date": newest.get("date") if newest is not oldest else cur.get("as_of") or cur.get("date"),
                "verdict": _verdict_equal(then_v, now_v),
            }
        )

    # 板块 priority：当时 high 列表 vs 后来
    then_secs = {
        str(s.get("sector") or ""): str(s.get("priority") or "").lower()
        for s in (oldest.get("sectors") or [])
        if s.get("sector")
    }
    now_secs_src = newest.get("sectors") if newest is not oldest else (cur.get("sectors") or [])
    now_secs = {
        str(s.get("sector") or ""): str(s.get("priority") or "").lower()
        for s in (now_secs_src or [])
        if s.get("sector")
    }
    for name in sorted(set(then_secs) | set(now_secs)):
        if not name:
            continue
        rows.append(
            {
                "dimension": "sector",
                "field": "priority",
                "sector": name,
                "then": then_secs.get(name) or "—",
                "then_date": oldest.get("date"),
                "now": now_secs.get(name) or "—",
                "now_date": newest.get("date") if newest is not oldest else None,
                "verdict": _verdict_equal(then_secs.get(name), now_secs.get(name)),
            }
        )
    return rows


def _sector_meta(sector: str, sector_analyses: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not sector:
        return {}
    for sec in sector_analyses or []:
        a = sec.get("analysis") or {}
        name = str(a.get("sector") or sec.get("sector") or "")
        if not name:
            continue
        if name == sector or sector in name or name in sector:
            return {
                "priority": a.get("priority"),
                "prosperity": a.get("prosperity"),
            }
        if theme_bucket(name) == theme_bucket(sector) and theme_bucket(sector) != "其他":
            return {
                "priority": a.get("priority"),
                "prosperity": a.get("prosperity"),
            }
    return {}


def _verdict_equal(a: Any, b: Any) -> str:
    sa = str(a or "").strip().lower()
    sb = str(b or "").strip().lower()
    if not sa or not sb or sa == "—" or sb == "—":
        return "unknown"
    if sa == sb or sa in sb or sb in sa:
        return "stable"
    return "changed"
