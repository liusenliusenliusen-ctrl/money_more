"""双源交叉校验：AkShare vs Tushare，不一致则降置信度。"""

from __future__ import annotations

from typing import Any

from money_more.data.as_of import parse_as_of, parse_record_date

# S12 阈值（中长线风险门禁；金融业负债率豁免）
_DEBT_WATCH = 75.0
_DEBT_BLOCK = 90.0
_PLEDGE_WATCH = 40.0
_PLEDGE_BLOCK = 60.0
_FINANCIAL_INDUSTRY_KEYS = ("银行", "保险", "证券", "多元金融", "金融", "信托", "期货")
_REDUCE_ANN_KEYS = ("减持", "股份减持", "拟减持", "减持计划")


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
    """A 股硬门禁：ST、近涨跌停、停牌近似、解禁、负债/质押/减持。"""
    reasons: list[str] = []
    quote = ak_snap.get("quote") or {}
    name = str(quote.get("名称") or quote.get("name") or "")
    hist = ak_snap.get("history") or {}
    chg = _f(hist.get("change_pct") or quote.get("涨跌幅"))
    intel = ak_snap.get("intelligence") or {}
    ts = tushare_bundle or {}

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

    # 解禁临近：30 日内窗口强制观察（S9）
    floats = ts.get("share_float") or []
    if floats:
        as_of = parse_as_of(ak_snap.get("as_of"))
        near: list[str] = []
        for item in floats:
            if not isinstance(item, dict):
                continue
            d = parse_record_date(
                item,
                date_keys=(
                    "float_date",
                    "解禁日期",
                    "date",
                    "日期",
                    "公告日期",
                    "trade_date",
                ),
            )
            if d is None:
                continue
            delta = (d - as_of).days
            if 0 <= delta <= 30:
                near.append(d.isoformat())
        if near:
            force_watch = True
            reasons.append("解禁临近(≤30日): " + "、".join(dict.fromkeys(near)))
        else:
            reasons.append(f"存在解禁记录 {len(floats)} 条，请人工核对")

    forecasts = ts.get("forecast") or []
    if forecasts:
        reasons.append(f"存在业绩预告 {len(forecasts)} 条")
        bomb_keys = (
            "预减",
            "首亏",
            "续亏",
            "增亏",
            "大幅下降",
            "由盈转亏",
            "亏损",
            "下滑",
            "不及预期",
            "预警",
        )
        bomb_hits: list[str] = []
        for item in forecasts[:6]:
            if not isinstance(item, dict):
                text = str(item)
            else:
                text = " ".join(
                    str(item.get(k) or "")
                    for k in ("type", "type_name", "p_change_min", "p_change_max", "summary", "title", "content")
                )
            for k in bomb_keys:
                if k in text:
                    bomb_hits.append(k)
                    break
        if bomb_hits:
            block_buy = True
            force_watch = True
            reasons.append("业绩预告偏空硬门禁: " + "、".join(dict.fromkeys(bomb_hits)))

    # —— S12：负债率 / 质押 / 减持 ——
    debt_hit = _gate_debt_ratio(ts, intel, name)
    if debt_hit:
        force_watch = force_watch or debt_hit["force_watch"]
        block_buy = block_buy or debt_hit["block_buy"]
        reasons.extend(debt_hit["reasons"])

    pledge_hit = _gate_pledge(intel)
    if pledge_hit:
        force_watch = force_watch or pledge_hit["force_watch"]
        block_buy = block_buy or pledge_hit["block_buy"]
        reasons.extend(pledge_hit["reasons"])

    reduce_hit = _gate_share_reduce(ts, intel, ak_snap)
    if reduce_hit:
        force_watch = force_watch or reduce_hit["force_watch"]
        block_buy = block_buy or reduce_hit["block_buy"]
        reasons.extend(reduce_hit["reasons"])

    return {
        "block_buy": block_buy,
        "force_watch": force_watch,
        "reasons": reasons,
    }


def _is_financial_name(*texts: str) -> bool:
    blob = " ".join(texts)
    return any(k in blob for k in _FINANCIAL_INDUSTRY_KEYS)


def _gate_debt_ratio(
    ts: dict[str, Any],
    intel: dict[str, Any],
    name: str,
) -> dict[str, Any] | None:
    fina = (ts.get("financials") or {}).get("indicators") or []
    row = fina[0] if fina and isinstance(fina[0], dict) else {}
    debt = _f(row.get("debt_to_assets") or row.get("debt_to_asset"))
    if debt is not None and 0 < debt <= 1.5:
        # 部分接口给 0–1 小数
        debt = debt * 100
    if debt is None:
        return None
    industry = str((intel.get("pledge_ratio") or {}).get("industry") or "")
    if _is_financial_name(name, industry, str(row.get("industry") or "")):
        return {
            "force_watch": False,
            "block_buy": False,
            "reasons": [f"负债率{debt:.1f}%（金融业豁免硬门禁）"],
        }
    if debt >= _DEBT_BLOCK:
        return {
            "force_watch": True,
            "block_buy": True,
            "reasons": [f"资产负债率过高硬门禁: {debt:.1f}%≥{_DEBT_BLOCK:.0f}%"],
        }
    if debt >= _DEBT_WATCH:
        return {
            "force_watch": True,
            "block_buy": False,
            "reasons": [f"资产负债率偏高: {debt:.1f}%≥{_DEBT_WATCH:.0f}%"],
        }
    return None


def _gate_pledge(intel: dict[str, Any]) -> dict[str, Any] | None:
    pledge = intel.get("pledge_ratio") or {}
    ratio = _f(pledge.get("ratio"))
    if ratio is None:
        return None
    if ratio >= _PLEDGE_BLOCK:
        return {
            "force_watch": True,
            "block_buy": True,
            "reasons": [f"股权质押比例过高硬门禁: {ratio:.1f}%≥{_PLEDGE_BLOCK:.0f}%"],
        }
    if ratio >= _PLEDGE_WATCH:
        return {
            "force_watch": True,
            "block_buy": False,
            "reasons": [f"股权质押比例偏高: {ratio:.1f}%≥{_PLEDGE_WATCH:.0f}%"],
        }
    return None


def _gate_share_reduce(
    ts: dict[str, Any],
    intel: dict[str, Any],
    ak_snap: dict[str, Any],
) -> dict[str, Any] | None:
    reasons: list[str] = []
    force_watch = False
    recent = intel.get("recent_share_reduce") or []
    if recent:
        force_watch = True
        sample = recent[0] if isinstance(recent[0], dict) else {}
        who = str(sample.get("变动股东") or "")[:20]
        reasons.append(f"近窗股东减持记录×{len(recent)}" + (f"（{who}）" if who else ""))

    as_of = parse_as_of(ak_snap.get("as_of"))
    anns = list(ts.get("announcements") or []) + list(ts.get("announcements_extended") or [])
    hit_titles: list[str] = []
    for item in anns[:20]:
        if not isinstance(item, dict):
            text = str(item)
            d = None
        else:
            text = " ".join(
                str(item.get(k) or "")
                for k in ("title", "标题", "ann_title", "content", "name")
            )
            d = parse_record_date(item, date_keys=("ann_date", "公告日期", "date", "日期", "pub_time"))
        if "增持" in text and "减持" not in text:
            continue
        if not any(k in text for k in _REDUCE_ANN_KEYS):
            continue
        if d is not None and (as_of - d).days > 60:
            continue
        title = text.strip()[:40] or "减持公告"
        hit_titles.append(title)
    if hit_titles:
        force_watch = True
        reasons.append("减持类公告: " + "；".join(list(dict.fromkeys(hit_titles))[:3]))

    if not reasons:
        return None
    return {"force_watch": force_watch, "block_buy": False, "reasons": reasons}


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
