"""盈利预期修正：业绩预告方向 + 财务指标趋势（主线基本面）。"""

from __future__ import annotations

from typing import Any

from money_more.data.fetcher import _safe_float

_UP_KEYS = ("预增", "略增", "扭亏", "续盈", "减亏", "超预期", "增长")
_DOWN_KEYS = ("预减", "略减", "首亏", "续亏", "增亏", "由盈转亏", "下滑", "下降", "不及预期")


def assess_earnings_revision(
    tushare_bundle: dict[str, Any] | None,
    ak_snap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回 revision_bias / signal / evidence，供个股研究与决策。"""
    ts = tushare_bundle or {}
    forecasts = list(ts.get("forecast") or [])
    indicators = list(((ts.get("financials") or {}).get("indicators") or []))

    fc_bias, fc_evidence = _forecast_bias(forecasts)
    fina_bias, fina_evidence = _fina_bias(indicators)
    data_source = "tushare" if (forecasts or indicators) else "none"

    if fina_bias == "none" and not forecasts:
        ak_bias, ak_evidence = _ak_fina_bias(ak_snap)
        if ak_bias != "none":
            fina_bias, fina_evidence = ak_bias, ak_evidence
            data_source = "akshare"

    # 综合：预告优先（更接近「预期修正」），财务趋势作验证
    if fc_bias in ("upgrade", "downgrade"):
        bias = fc_bias
    elif fina_bias in ("upgrade", "downgrade"):
        bias = fina_bias
    elif fc_bias == "mixed" or fina_bias == "mixed":
        bias = "mixed"
    elif fc_bias == "none" and fina_bias == "none":
        bias = "none"
    else:
        bias = "neutral"

    # 冲突时降为 mixed
    if fc_bias in ("upgrade", "downgrade") and fina_bias in ("upgrade", "downgrade") and fc_bias != fina_bias:
        bias = "mixed"

    signal = {
        "upgrade": "positive",
        "downgrade": "negative",
        "mixed": "mixed",
        "neutral": "neutral",
        "none": "unknown",
    }.get(bias, "unknown")

    evidence = (fc_evidence + fina_evidence)[:6]
    confidence = 0.35
    if fc_bias in ("upgrade", "downgrade"):
        confidence = 0.7
    elif fina_bias in ("upgrade", "downgrade"):
        confidence = 0.55
    elif bias == "mixed":
        confidence = 0.4

    note = {
        "positive": "盈利预期偏上修/景气改善线索，可提高质量因子权重，仍需估值与资金验证。",
        "negative": "盈利预期偏下修/恶化线索，主线应降仓或观望，失效条件优先看业绩。",
        "mixed": "预告与财务趋势冲突或方向不清，降低置信度。",
        "neutral": "未见明显预期修正。",
        "unknown": "缺少业绩预告/财务指标，预期修正维度不可用。",
    }.get(signal, "")

    return {
        "revision_bias": bias,
        "signal": signal,
        "confidence": confidence,
        "forecast_bias": fc_bias,
        "fina_bias": fina_bias,
        "data_source": data_source,
        "evidence": evidence,
        "note": note,
        "layer": "mainline",
    }


def _forecast_bias(items: list[Any]) -> tuple[str, list[str]]:
    if not items:
        return "none", []
    up = down = 0
    evidence: list[str] = []
    for item in items[:6]:
        if not isinstance(item, dict):
            text = str(item)
        else:
            text = " ".join(
                str(item.get(k) or "")
                for k in ("type", "type_name", "summary", "title", "content", "p_change_min", "p_change_max")
            )
        label = "neutral"
        if any(k in text for k in _DOWN_KEYS):
            down += 1
            label = "down"
        elif any(k in text for k in _UP_KEYS):
            up += 1
            label = "up"
        # 数值区间
        pmin = _safe_float(item.get("p_change_min") if isinstance(item, dict) else None)
        pmax = _safe_float(item.get("p_change_max") if isinstance(item, dict) else None)
        if pmin is not None and pmax is not None:
            mid = (pmin + pmax) / 2
            if mid <= -10:
                down += 1
                label = "down"
            elif mid >= 10:
                up += 1
                label = "up"
        snip = text.replace("\n", " ").strip()[:80]
        if snip:
            evidence.append(f"预告[{label}]: {snip}")
    if up and down:
        return "mixed", evidence[:4]
    if down:
        return "downgrade", evidence[:4]
    if up:
        return "upgrade", evidence[:4]
    return "neutral", evidence[:2]


def _ak_fina_bias(ak_snap: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Tushare 缺 forecast/fina 时用已采集 AkShare 财务指标/摘要回填。"""
    fin = (ak_snap or {}).get("financial") or {}
    indicators = list(fin.get("indicators") or [])
    if indicators:
        normalized = [_normalize_ak_indicator_row(r) for r in indicators if isinstance(r, dict)]
        bias, evidence = _fina_bias(normalized)
        if bias != "none":
            return bias, [f"[AkShare指标] {e}" for e in evidence]
    abstract = list(fin.get("abstract") or [])
    if abstract:
        return _ak_abstract_bias(abstract)
    return "none", []


def _normalize_ak_indicator_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    yoy = _find_row_metric(row, "净利润同比增长", "扣非净利润同比", "归属净利润同比")
    roe = _find_row_metric(row, "净资产收益率", "ROE")
    gross = _find_row_metric(row, "销售毛利率", "毛利率")
    if yoy is not None:
        out["netprofit_yoy"] = yoy
    if roe is not None:
        out["roe"] = roe
    if gross is not None:
        out["grossprofit_margin"] = gross
    return out


def _find_row_metric(row: dict[str, Any], *needles: str) -> float | None:
    for key, val in row.items():
        key_s = str(key)
        if any(n in key_s for n in needles):
            fv = _safe_float(val)
            if fv is not None:
                return fv
    return None


def _ak_abstract_bias(rows: list[Any]) -> tuple[str, list[str]]:
    """解析 AkShare 财务摘要透视表（行=指标，列=报告期）。"""
    evidence: list[str] = []
    score = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        option = str(row.get("选项") or row.get("指标") or "")
        if not any(k in option for k in ("净利润同比", "扣非净利润同比", "归属净利润同比", "营业总收入同比")):
            continue
        periods: list[tuple[str, float]] = []
        for key, val in row.items():
            if str(key) in ("选项", "指标"):
                continue
            fv = _safe_float(val)
            if fv is not None:
                periods.append((str(key), fv))
        if not periods:
            continue
        periods.sort(key=lambda x: x[0], reverse=True)
        latest_val = periods[0][1]
        evidence.append(f"[AkShare摘要]{option}最新={latest_val}")
        if len(periods) >= 2:
            delta = latest_val - periods[1][1]
            evidence.append(f"[AkShare摘要]{option}环比Δ={round(delta, 2)}")
            if "同比" in option:
                if delta >= 3:
                    score += 1
                elif delta <= -3:
                    score -= 1
        elif "同比" in option:
            if latest_val >= 20:
                score += 1
            elif latest_val <= -10:
                score -= 1

    if score >= 1:
        return "upgrade", evidence[:4]
    if score <= -1:
        return "downgrade", evidence[:4]
    if evidence:
        return "neutral", evidence[:3]
    return "none", []


def _fina_bias(indicators: list[Any]) -> tuple[str, list[str]]:
    if not indicators:
        return "none", []
    rows = [r for r in indicators if isinstance(r, dict)]
    if not rows:
        return "none", []

    # Tushare fina_indicator 通常按期倒序
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    evidence: list[str] = []
    score = 0

    for key, label in (
        ("netprofit_yoy", "净利YoY"),
        ("dt_netprofit_yoy", "扣非净利YoY"),
        ("roe", "ROE"),
        ("grossprofit_margin", "毛利率"),
    ):
        cur = _safe_float(latest.get(key))
        if cur is None:
            continue
        evidence.append(f"{label}最新={cur}")
        if prev:
            old = _safe_float(prev.get(key))
            if old is not None:
                delta = cur - old
                evidence.append(f"{label}环比Δ={round(delta, 2)}")
                if key.endswith("yoy") or key == "roe":
                    if delta >= 3:
                        score += 1
                    elif delta <= -3:
                        score -= 1
        else:
            if key.endswith("yoy"):
                if cur >= 20:
                    score += 1
                elif cur <= -10:
                    score -= 1

    if score >= 1:
        return "upgrade", evidence[:4]
    if score <= -1:
        return "downgrade", evidence[:4]
    if evidence:
        return "neutral", evidence[:3]
    return "none", []
