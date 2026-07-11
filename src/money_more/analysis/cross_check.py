"""双源交叉校验：AkShare vs Tushare，不一致则降置信度。"""

from __future__ import annotations

from typing import Any


def cross_check_stock(
    ak_snap: dict[str, Any],
    tushare_bundle: dict[str, Any] | None,
    *,
    close_tol_pct: float = 1.0,
) -> dict[str, Any]:
    """比较收盘价 / PE / PB；返回 flags 与建议置信度折扣。"""
    result: dict[str, Any] = {
        "ok": True,
        "flags": [],
        "confidence_haircut": 0.0,
        "ak_close": None,
        "ts_close": None,
        "ak_pe": None,
        "ts_pe": None,
    }
    hist = ak_snap.get("history") or {}
    quote = ak_snap.get("quote") or {}
    ak_close = _f(hist.get("close") or quote.get("最新价") or quote.get("收盘"))
    ak_pe = _f(quote.get("市盈率-动态") or quote.get("市盈率"))
    ak_pb = _f(quote.get("市净率"))

    ts = tushare_bundle or {}
    val = (ts.get("valuation") or {}).get("latest") or {}
    # daily_basic 无 close 时可能只有 pe/pb；部分接口带 close
    ts_close = _f(val.get("close"))
    ts_pe = _f(val.get("pe_ttm") or val.get("pe"))
    ts_pb = _f(val.get("pb"))

    result["ak_close"] = ak_close
    result["ts_close"] = ts_close
    result["ak_pe"] = ak_pe
    result["ts_pe"] = ts_pe
    result["ak_pb"] = ak_pb
    result["ts_pb"] = ts_pb

    if not ts or ts.get("errors"):
        if not val:
            result["flags"].append("tushare_valuation_missing")
            result["confidence_haircut"] += 0.05
        # 有错误但不一定失败
        errs = ts.get("errors") or []
        if errs:
            result["flags"].append("tushare_errors")

    if ak_close and ts_close:
        diff = abs(ak_close - ts_close) / max(abs(ak_close), 1e-9) * 100
        result["close_diff_pct"] = round(diff, 3)
        if diff > close_tol_pct:
            result["ok"] = False
            result["flags"].append(f"close_mismatch_{diff:.2f}pct")
            result["confidence_haircut"] += 0.15

    if ak_pe and ts_pe and ak_pe > 0 and ts_pe > 0:
        pe_diff = abs(ak_pe - ts_pe) / max(abs(ak_pe), 1e-9) * 100
        result["pe_diff_pct"] = round(pe_diff, 2)
        if pe_diff > 25:
            result["flags"].append(f"pe_mismatch_{pe_diff:.0f}pct")
            result["confidence_haircut"] += 0.08

    if not ak_close and not ts_close:
        result["ok"] = False
        result["flags"].append("no_price_either_source")
        result["confidence_haircut"] += 0.25

    result["confidence_haircut"] = round(min(0.5, result["confidence_haircut"]), 3)
    return result


def apply_hard_gates(
    code: str,
    ak_snap: dict[str, Any],
    tushare_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A 股硬门禁：ST、近涨跌停、停牌近似。返回 block_buy / force_watch / reasons。"""
    reasons: list[str] = []
    quote = ak_snap.get("quote") or {}
    name = str(quote.get("名称") or quote.get("name") or "")
    hist = ak_snap.get("history") or {}
    chg = _f(hist.get("change_pct") or quote.get("涨跌幅"))

    block_buy = False
    force_watch = False

    if "ST" in name.upper() or name.startswith("*"):
        block_buy = True
        force_watch = True
        reasons.append(f"ST/*ST 标的: {name}")

    # 近涨跌停（主板约 10%，创业板/科创 20% 粗判）
    limit = 9.5
    if code.startswith(("3", "68")):
        limit = 19.5
    if chg is not None and abs(chg) >= limit:
        force_watch = True
        reasons.append(f"近涨跌停 change_pct={chg}")
        if abs(chg) >= limit:
            block_buy = True

    # 一字板/严重异常波动：振幅极小且涨跌停
    amp = _f(quote.get("振幅"))
    if chg is not None and amp is not None and abs(chg) >= limit * 0.95 and amp < 0.5:
        block_buy = True
        force_watch = True
        reasons.append(f"疑似一字板 振幅={amp}")

    # 成交量异常为 0 可能停牌
    vol = _f(hist.get("volume") or quote.get("成交量"))
    if vol is not None and vol <= 0:
        block_buy = True
        force_watch = True
        reasons.append("成交量≈0，疑似停牌")

    # 解禁临近（若有 share_float）
    floats = (tushare_bundle or {}).get("share_float") or []
    if floats:
        reasons.append(f"存在解禁记录 {len(floats)} 条，请人工核对")

    forecasts = (tushare_bundle or {}).get("forecast") or []
    if forecasts:
        reasons.append(f"存在业绩预告 {len(forecasts)} 条")

    return {
        "block_buy": block_buy,
        "force_watch": force_watch,
        "reasons": reasons,
    }


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
