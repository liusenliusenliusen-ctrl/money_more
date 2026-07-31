"""股债相对价值（ERP）：用指数盈利收益率相对国债约束总仓上限。"""

from __future__ import annotations

from datetime import date
from typing import Any

from money_more.data.as_of import parse_as_of
from money_more.data.fetcher import _safe_float
from money_more.utils.logging_util import setup_logging

log = setup_logging()

# 默认映射：ERP（bp）→ 总仓上限%（再与 trading.max_total 取小）
DEFAULT_ERP_CEILINGS = (
    (600, 80.0),  # attractive
    (400, 65.0),  # neutral-high
    (200, 50.0),  # neutral-low
    (0, 35.0),  # expensive
)


def compute_erp(
    *,
    cn_10y_pct: float | None,
    index_pe_ttm: float | None,
    index_name: str = "沪深300",
    index_code: str = "000300.SH",
    max_total_cap: float = 80.0,
    ceilings: list[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """ERP = 盈利收益率 − 中国10Y（百分点）；erp_bp = ERP×100。"""
    out: dict[str, Any] = {
        "index": index_name,
        "index_code": index_code,
        "pe_ttm": index_pe_ttm,
        "earnings_yield_pct": None,
        "cn_10y_pct": cn_10y_pct,
        "erp_pct": None,
        "erp_bp": None,
        "regime": "unknown",
        "implied_max_total_pct": float(max_total_cap),
        "implied_min_cash_pct": 0.0,
        "note": "",
        "ok": False,
    }
    if index_pe_ttm is None or index_pe_ttm <= 0 or cn_10y_pct is None:
        out["note"] = "缺少指数PE或中国10Y，股债性价比跳过"
        return out

    ey = 100.0 / float(index_pe_ttm)
    erp = ey - float(cn_10y_pct)
    erp_bp = erp * 100.0
    regime, implied = _map_ceiling(erp_bp, ceilings or list(DEFAULT_ERP_CEILINGS))
    implied = min(float(max_total_cap), float(implied))
    out.update(
        {
            "earnings_yield_pct": round(ey, 2),
            "erp_pct": round(erp, 2),
            "erp_bp": round(erp_bp, 1),
            "regime": regime,
            "implied_max_total_pct": round(implied, 1),
            "implied_min_cash_pct": round(max(0.0, 100.0 - implied), 1),
            "ok": True,
            "note": (
                f"{index_name} PE={index_pe_ttm:.1f} · 盈利收益率{ey:.2f}% · "
                f"中国10Y {cn_10y_pct:.2f}% · ERP {erp_bp:.0f}bp → "
                f"总仓上限建议 {implied:.0f}%（regime={regime}）"
            ),
        }
    )
    return out


def fetch_hs300_pe_ttm(as_of: date | str | None = None) -> dict[str, Any]:
    """拉取沪深300 PE（优先 AkShare stock_index_pe_lg）。失败不抛。"""
    as_of_d = parse_as_of(as_of)
    out: dict[str, Any] = {
        "pe_ttm": None,
        "as_of": as_of_d.isoformat(),
        "source": None,
        "errors": [],
    }
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_index_pe_lg(symbol="沪深300")
        if df is None or df.empty:
            out["errors"].append("stock_index_pe_lg: empty")
            return out
        work = df.copy()
        date_col = None
        for c in ("日期", "date", "trade_date"):
            if c in work.columns:
                date_col = c
                break
        pe_col = None
        for c in ("滚动市盈率", "市盈率", "pe_ttm", "PE", "pe"):
            if c in work.columns:
                pe_col = c
                break
        if date_col is None or pe_col is None:
            out["errors"].append(
                f"stock_index_pe_lg: missing cols date={date_col} pe={pe_col} cols={list(work.columns)[:8]}"
            )
            return out
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        work[pe_col] = pd.to_numeric(work[pe_col], errors="coerce")
        work = work.dropna(subset=[date_col, pe_col])
        work = work[work[date_col] <= pd.Timestamp(as_of_d)]
        if work.empty:
            out["errors"].append("stock_index_pe_lg: no rows <= as_of")
            return out
        latest = work.sort_values(date_col).iloc[-1]
        pe = _safe_float(latest.get(pe_col))
        out["pe_ttm"] = pe
        out["trade_date"] = (
            latest[date_col].date().isoformat()
            if hasattr(latest[date_col], "date")
            else str(latest[date_col])[:10]
        )
        out["source"] = "ak.stock_index_pe_lg"
    except Exception as exc:
        out["errors"].append(f"stock_index_pe_lg: {exc}")
        log.warning("fetch_hs300_pe_ttm failed: %s", exc)
    return out


def build_equity_bond_from_macro(
    global_liquidity: dict[str, Any] | None,
    *,
    as_of: date | str | None = None,
    max_total_cap: float = 80.0,
    enabled: bool = True,
    index_pe_ttm: float | None = None,
) -> dict[str, Any]:
    """从宏观流动性 + 指数PE 组装股债性价比结果。"""
    if not enabled:
        return {
            "ok": False,
            "enabled": False,
            "regime": "unknown",
            "note": "equity_bond.enabled=false",
            "implied_max_total_pct": float(max_total_cap),
        }
    gl = global_liquidity or {}
    cn = _safe_float((gl.get("cn_10y") or {}).get("latest"))
    pe_info: dict[str, Any] = {}
    pe = index_pe_ttm
    if pe is None:
        pe_info = fetch_hs300_pe_ttm(as_of)
        pe = pe_info.get("pe_ttm")
    erp = compute_erp(
        cn_10y_pct=cn,
        index_pe_ttm=pe,
        max_total_cap=max_total_cap,
    )
    erp["enabled"] = True
    erp["pe_source"] = pe_info.get("source")
    erp["pe_trade_date"] = pe_info.get("trade_date")
    if pe_info.get("errors"):
        erp["errors"] = pe_info["errors"]
    return erp


def _map_ceiling(
    erp_bp: float,
    ceilings: list[tuple[float, float]],
) -> tuple[str, float]:
    """ceilings: 按 erp_bp 下限降序 [(min_bp, max_total), ...]。"""
    ordered = sorted(ceilings, key=lambda x: -x[0])
    for min_bp, cap in ordered:
        if erp_bp >= min_bp:
            if min_bp >= 600:
                return "attractive", cap
            if min_bp >= 400:
                return "neutral", cap
            if min_bp >= 200:
                return "cautious", cap
            return "expensive", cap
    return "expensive", ordered[-1][1] if ordered else 35.0
