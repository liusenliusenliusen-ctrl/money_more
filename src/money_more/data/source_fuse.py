"""双源融合：AkShare ↔ Tushare，按「可不一致」约定合并。

原则：
- 保留 source / as_of / period，禁止静默覆盖
- 门禁类（质押、减持）→ 保守合并
- 序列类（宏观、两融）→ 主源优先 + 期次校验；冲突标 conflict，不平均
"""

from __future__ import annotations

from datetime import date
from typing import Any

from money_more.data.as_of import parse_macro_period_date, parse_record_date
from money_more.data.fetcher import _safe_float


def fuse_pledge(
    ak: dict[str, Any] | None,
    ts: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """质押比例：ratio = max(可用源)；附 sources / agreement。"""
    ak = ak if isinstance(ak, dict) and ak else None
    ts = ts if isinstance(ts, dict) and ts else None
    if not ak and not ts:
        return None

    ak_ratio = _safe_float((ak or {}).get("ratio"))
    ts_ratio = _safe_float((ts or {}).get("ratio"))
    ratios = [r for r in (ak_ratio, ts_ratio) if r is not None]
    if not ratios:
        return None

    ratio = max(ratios)
    sources: list[str] = []
    if ak_ratio is not None:
        sources.append("akshare")
    if ts_ratio is not None:
        sources.append("tushare")

    if ak_ratio is not None and ts_ratio is not None:
        agreement = "match" if abs(ak_ratio - ts_ratio) <= 0.5 else "conflict"
        primary = "merged"
    elif ts_ratio is not None:
        agreement = "single"
        primary = "tushare"
    else:
        agreement = "single"
        primary = "akshare"

    base = dict(ak or ts or {})
    if ts and (not ak or primary == "tushare"):
        # 补齐 Tushare 字段（不覆盖已有非空 Ak 字段）
        for k, v in ts.items():
            if k not in base or base.get(k) in (None, ""):
                base[k] = v

    out = {
        **base,
        "ratio": ratio,
        "source": primary if agreement != "conflict" else "merged",
        "sources": sources,
        "agreement": agreement,
        "ak_ratio": ak_ratio,
        "ts_ratio": ts_ratio,
        "as_of": (ak or {}).get("trade_date")
        or (ak or {}).get("as_of")
        or (ts or {}).get("end_date")
        or (ts or {}).get("as_of"),
    }
    return out


def map_ts_holder_trade_to_reduce(item: dict[str, Any]) -> dict[str, Any] | None:
    """Tushare stk_holdertrade 一行 → recent_share_reduce 兼容结构。"""
    if not isinstance(item, dict):
        return None
    in_de = str(item.get("in_de") or "").upper()
    if in_de and in_de != "DE":
        return None
    # 无 in_de 时看文本是否含减持
    blob = " ".join(str(item.get(k) or "") for k in ("in_de", "holder_name", "change_vol"))
    if in_de != "DE" and "减" not in blob:
        return None

    vol = item.get("change_vol")
    ratio = item.get("change_ratio")
    ann = item.get("ann_date") or item.get("公告日期")
    return {
        "变动股东": item.get("holder_name") or item.get("holder_type") or "",
        "变动数量": f"减持{vol}" if vol is not None else "减持",
        "变动途径": "增减持",
        "公告日期": _fmt_ymd(ann),
        "change_ratio": ratio,
        "change_vol": vol,
        "source": "tushare_stk_holdertrade",
        "in_de": "DE",
        "raw": {k: item.get(k) for k in ("ts_code", "holder_type", "after_share", "avg_price") if k in item},
    }


def fuse_share_reduce(
    ak_items: list[dict[str, Any]] | None,
    ts_items: list[dict[str, Any]] | None,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """近窗减持：并集去重；任一侧有即保留（门禁侧 force_watch）。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in ak_items or []:
        if not isinstance(item, dict):
            continue
        key = _reduce_dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        row = dict(item)
        row.setdefault("source", "akshare_ths")
        out.append(row)

    for raw in ts_items or []:
        mapped = map_ts_holder_trade_to_reduce(raw) if "变动股东" not in (raw or {}) else raw
        if not mapped:
            continue
        key = _reduce_dedupe_key(mapped)
        if key in seen:
            continue
        seen.add(key)
        out.append(mapped)

    # 近→远
    def _sort_key(row: dict[str, Any]) -> date:
        d = parse_record_date(row, date_keys=("公告日期", "ann_date", "date", "日期", "变动期间"))
        return d or date.min

    out.sort(key=_sort_key, reverse=True)
    return out[:limit]


def fuse_macro_series(
    ak_recs: list[dict[str, Any]] | None,
    ts_recs: list[dict[str, Any]] | None,
    *,
    primary: str = "tushare",
    period_tol_months: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """宏观序列融合：主源优先；期次差过大标 conflict，不平均数值。"""
    ak_recs = [r for r in (ak_recs or []) if isinstance(r, dict)]
    ts_recs = [r for r in (ts_recs or []) if isinstance(r, dict)]
    meta: dict[str, Any] = {
        "primary": primary,
        "agreement": "empty",
        "sources": {},
    }
    if ak_recs:
        meta["sources"]["akshare"] = {
            "latest_period": _period_label(ak_recs[0]) if ak_recs else None,
            "n": len(ak_recs),
        }
    if ts_recs:
        meta["sources"]["tushare"] = {
            "latest_period": _period_label(ts_recs[0]) if ts_recs else None,
            "n": len(ts_recs),
        }

    if not ak_recs and not ts_recs:
        return [], meta
    if not ak_recs:
        meta["agreement"] = "single"
        meta["primary"] = "tushare"
        return ts_recs, meta
    if not ts_recs:
        meta["agreement"] = "single"
        meta["primary"] = "akshare"
        return ak_recs, meta

    ak_p = parse_macro_period_date(ak_recs[0])
    ts_p = parse_macro_period_date(ts_recs[0])
    if ak_p and ts_p:
        month_gap = abs((ak_p.year - ts_p.year) * 12 + (ak_p.month - ts_p.month))
        if month_gap > period_tol_months:
            meta["agreement"] = "conflict"
            meta["period_gap_months"] = month_gap
        else:
            meta["agreement"] = "match"
            meta["period_gap_months"] = month_gap
    else:
        meta["agreement"] = "unknown"

    if primary == "tushare":
        series = ts_recs
        meta["primary"] = "tushare"
    elif primary == "akshare":
        series = ak_recs
        meta["primary"] = "akshare"
    else:
        # 期次更新的一侧
        if ak_p and ts_p and ak_p > ts_p:
            series = ak_recs
            meta["primary"] = "akshare"
        else:
            series = ts_recs
            meta["primary"] = "tushare"
    return series, meta


def fuse_margin_trend(
    ak: dict[str, Any] | None,
    ts: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """市场两融：Ak 为主，失败用 TS；比近 5 日变化方向。"""
    ak = ak if isinstance(ak, dict) and ak else None
    ts = ts if isinstance(ts, dict) and ts else None
    if not ak and not ts:
        return None
    if ak and not ts:
        out = dict(ak)
        out.setdefault("source", "akshare")
        out["agreement"] = "single"
        return out
    if ts and not ak:
        out = dict(ts)
        out.setdefault("source", "tushare")
        out["agreement"] = "single"
        return out

    assert ak is not None and ts is not None
    ak_chg = _safe_float(ak.get("financing_balance_change_5d_pct"))
    ts_chg = _safe_float(ts.get("financing_balance_change_5d_pct"))
    agreement = "unknown"
    if ak_chg is not None and ts_chg is not None:
        if (ak_chg == 0 and ts_chg == 0) or (ak_chg * ts_chg > 0) or abs(ak_chg - ts_chg) <= 0.5:
            agreement = "match"
        else:
            agreement = "conflict"

    out = dict(ak)
    out["source"] = "akshare"
    out["sources"] = ["akshare", "tushare"]
    out["agreement"] = agreement
    out["tushare"] = {
        "latest": ts.get("latest"),
        "financing_balance_change_5d_pct": ts_chg,
        "as_of": ts.get("as_of") or ts.get("trade_date"),
    }
    if agreement == "conflict":
        out["note"] = "Ak/Tushare 近5日融资余额变化方向不一致，以 Ak 主序列为准"
    return out


def fuse_margin_detail(
    ak_items: list[dict[str, Any]] | None,
    ts_items: list[dict[str, Any]] | None,
    *,
    prefer: str = "tushare",
) -> list[dict[str, Any]]:
    """个股两融明细：默认 Tushare 优先（比 Ak 按日盲试稳）。"""
    ak_items = [x for x in (ak_items or []) if isinstance(x, dict)]
    ts_items = [x for x in (ts_items or []) if isinstance(x, dict)]
    if prefer == "tushare" and ts_items:
        out = []
        for row in ts_items:
            r = dict(row)
            r.setdefault("source", "tushare_margin_detail")
            out.append(r)
        return out
    if ak_items:
        out = []
        for row in ak_items:
            r = dict(row)
            r.setdefault("source", "akshare_sse")
            out.append(r)
        return out
    if ts_items:
        return [{**r, "source": r.get("source") or "tushare_margin_detail"} for r in ts_items]
    return []


def _reduce_dedupe_key(item: dict[str, Any]) -> str:
    who = str(item.get("变动股东") or item.get("holder_name") or "")[:24]
    d = str(item.get("公告日期") or item.get("ann_date") or "")[:10]
    vol = str(item.get("变动数量") or item.get("change_vol") or "")[:20]
    return f"{d}|{who}|{vol}"


def _period_label(rec: dict[str, Any]) -> str | None:
    for k in ("月份", "month", "日期", "date"):
        if rec.get(k) not in (None, ""):
            return str(rec.get(k))
    p = parse_macro_period_date(rec)
    return p.strftime("%Y%m") if p else None


def _fmt_ymd(raw: Any) -> str:
    if raw is None:
        return ""
    text = str(raw).strip().replace("-", "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(raw)
