"""全球流动性硬指标：美债收益率 + 美元/人民币代理（主线宏观）。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import akshare as ak
import pandas as pd

from money_more.data.as_of import parse_as_of
from money_more.data.fetcher import _safe_float
from money_more.utils.logging_util import setup_logging

log = setup_logging()


def fetch_global_liquidity(as_of: date | str | None = None) -> dict[str, Any]:
    """拉取并压缩全球流动性摘要，失败不抛出。"""
    as_of_d = parse_as_of(as_of)
    out: dict[str, Any] = {
        "as_of": as_of_d.isoformat(),
        "stance": "unknown",
        "us_10y": {},
        "us_2s10s": {},
        "cn_10y": {},
        "usd_cny": {},
        "series_tail": [],
        "signals": [],
        "a_share_implication": "",
        "errors": [],
        "source": [],
    }

    bond = _fetch_bond_rates(as_of_d)
    if bond.get("error"):
        out["errors"].append(bond["error"])
    else:
        out["source"].append("bond_zh_us_rate")
        out.update({k: bond[k] for k in ("us_10y", "us_2s10s", "cn_10y", "series_tail") if k in bond})
        out["signals"].extend(bond.get("signals") or [])

    fx = _fetch_usd_cny(as_of_d)
    if fx.get("error"):
        out["errors"].append(fx["error"])
    else:
        out["source"].append(fx.get("source") or "usd_cny")
        out["usd_cny"] = fx.get("usd_cny") or {}
        out["signals"].extend(fx.get("signals") or [])

    out["stance"] = _classify_stance(out)
    out["a_share_implication"] = _implication(out["stance"], out)
    out["plain_note"] = (
        f"全球流动性 stance=`{out['stance']}`；"
        f"美债10Y={((out.get('us_10y') or {}).get('latest'))} "
        f"Δ20d={((out.get('us_10y') or {}).get('change_20d_bp'))}bp；"
        f"USD/CNY Δ20d={((out.get('usd_cny') or {}).get('change_20d_pct'))}%"
    )
    return out


def _fetch_bond_rates(as_of: date) -> dict[str, Any]:
    start = (as_of - timedelta(days=150)).strftime("%Y%m%d")
    try:
        df = ak.bond_zh_us_rate(start_date=start)
    except Exception as exc:
        return {"error": f"bond_zh_us_rate: {exc}"}
    if df is None or df.empty:
        return {"error": "bond_zh_us_rate: empty"}

    work = df.copy()
    work["日期"] = pd.to_datetime(work["日期"], errors="coerce")
    work = work[work["日期"] <= pd.Timestamp(as_of)].dropna(subset=["日期"])
    if work.empty:
        return {"error": "bond_zh_us_rate: no rows <= as_of"}

    us10 = "美国国债收益率10年"
    us2 = "美国国债收益率2年"
    cn10 = "中国国债收益率10年"
    spread = "美国国债收益率10年-2年"
    for col in (us10, us2, cn10, spread):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    latest = work.iloc[-1]
    out: dict[str, Any] = {"signals": []}
    out["us_10y"] = _rate_stats(work, us10)
    out["cn_10y"] = _rate_stats(work, cn10)
    out["us_2s10s"] = _rate_stats(work, spread)

    # 中美利差（美-中）bp
    u = _safe_float(latest.get(us10))
    c = _safe_float(latest.get(cn10))
    if u is not None and c is not None:
        out["us_cn_10y_spread_bp"] = round((u - c) * 100, 1)

    # 信号
    chg20 = (out["us_10y"] or {}).get("change_20d_bp")
    if chg20 is not None:
        if chg20 >= 25:
            out["signals"].append(f"美债10Y 近20日上行 {chg20}bp（流动性趋紧代理）")
        elif chg20 <= -25:
            out["signals"].append(f"美债10Y 近20日下行 {chg20}bp（流动性缓和代理）")
    curve = (out["us_2s10s"] or {}).get("latest")
    if curve is not None and curve < 0:
        out["signals"].append(f"美债曲线倒挂(10Y-2Y={curve})")

    tail_cols = [c for c in ("日期", us10, us2, cn10, spread) if c == "日期" or c in work.columns]
    tail = work[tail_cols].tail(8)
    rows = []
    for _, r in tail.iterrows():
        rows.append(
            {
                "date": r["日期"].date().isoformat() if hasattr(r["日期"], "date") else str(r["日期"])[:10],
                "us_10y": _safe_float(r.get(us10)),
                "us_2y": _safe_float(r.get(us2)),
                "cn_10y": _safe_float(r.get(cn10)),
                "us_2s10s": _safe_float(r.get(spread)),
            }
        )
    out["series_tail"] = rows
    return out


def _rate_stats(df: pd.DataFrame, col: str) -> dict[str, Any]:
    if col not in df.columns:
        return {}
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty:
        return {}
    latest = float(s.iloc[-1])
    out: dict[str, Any] = {"latest": round(latest, 4), "unit": "%"}
    if len(s) >= 6:
        prev5 = float(s.iloc[-6])
        out["change_5d_bp"] = round((latest - prev5) * 100, 1)
    if len(s) >= 21:
        prev20 = float(s.iloc[-21])
        out["change_20d_bp"] = round((latest - prev20) * 100, 1)
    if len(s) >= 61:
        prev60 = float(s.iloc[-61])
        out["change_60d_bp"] = round((latest - prev60) * 100, 1)
    return out


def _fetch_usd_cny(as_of: date) -> dict[str, Any]:
    """用中行美元中间价/汇卖价近似美元强弱（对 A 股更直接）。"""
    fn = getattr(ak, "currency_boc_sina", None)
    if fn is None:
        return {"error": "currency_boc_sina unavailable"}
    try:
        # 新浪中行：symbol 美元
        df = fn(symbol="美元")
    except TypeError:
        try:
            df = fn()
        except Exception as exc:
            return {"error": f"currency_boc_sina: {exc}"}
    except Exception as exc:
        return {"error": f"currency_boc_sina: {exc}"}

    if df is None or df.empty:
        return {"error": "currency_boc_sina: empty"}

    work = df.copy()
    date_col = "日期" if "日期" in work.columns else work.columns[0]
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work = work[work[date_col] <= pd.Timestamp(as_of)].dropna(subset=[date_col])
    price_col = next(
        (c for c in ("央行中间价", "中行折算价", "中行钞卖价/汇卖价", "中行汇买价") if c in work.columns),
        None,
    )
    if not price_col or work.empty:
        return {"error": "currency_boc_sina: no price col"}

    work[price_col] = pd.to_numeric(work[price_col], errors="coerce")
    s = work.dropna(subset=[price_col]).sort_values(date_col)
    if s.empty:
        return {"error": "currency_boc_sina: no numeric rows"}
    latest = float(s[price_col].iloc[-1])
    # 中行中间价有时以「分」报价（如 717≈7.17），归一到元
    scale = 100.0 if latest > 50 else 1.0
    latest = latest / scale
    stats: dict[str, Any] = {"latest": round(latest, 4), "unit": "CNY per USD", "field": price_col}
    signals: list[str] = []
    if len(s) >= 21:
        prev = float(s[price_col].iloc[-21]) / scale
        if prev:
            chg = round((latest / prev - 1) * 100, 2)
            stats["change_20d_pct"] = chg
            if chg >= 1.0:
                signals.append(f"USD/CNY 近20日升值 {chg}%（人民币贬值压力）")
            elif chg <= -1.0:
                signals.append(f"USD/CNY 近20日回落 {chg}%（人民币偏强）")
    return {"usd_cny": stats, "signals": signals, "source": "currency_boc_sina"}


def _classify_stance(payload: dict[str, Any]) -> str:
    score = 0  # + 紧，- 松
    us = payload.get("us_10y") or {}
    for key, w in (("change_20d_bp", 1), ("change_60d_bp", 0.5)):
        v = us.get(key)
        if v is None:
            continue
        if v >= 25 * (1 if key == "change_20d_bp" else 1):
            score += w
        elif v <= -25 * (1 if key == "change_20d_bp" else 1):
            score -= w
    fx = payload.get("usd_cny") or {}
    chg = fx.get("change_20d_pct")
    if chg is not None:
        if chg >= 1.0:
            score += 0.5
        elif chg <= -1.0:
            score -= 0.5
    if score >= 1.0:
        return "tightening"
    if score <= -1.0:
        return "easing"
    if payload.get("us_10y") or payload.get("usd_cny"):
        return "mixed"
    return "unknown"


def _implication(stance: str, payload: dict[str, Any]) -> str:
    if stance == "tightening":
        return (
            "全球流动性偏紧代理占优：风险偏好易受压，成长/长久估值承压概率上升；"
            "A 股宜提高现金与高股息/防御权重，关注北向与汇率联动。"
        )
    if stance == "easing":
        return (
            "全球流动性缓和代理占优：风险资产估值修复空间打开；"
            "仍需看国内信用/政策是否共振，不宜单靠外因激进加仓。"
        )
    if stance == "mixed":
        return "利率与汇率信号分歧：主线维持均衡/偏防御，等待美债与人民币方向一致后再加风险敞口。"
    return "全球流动性硬指标不足，宏观外因置信度下调。"
