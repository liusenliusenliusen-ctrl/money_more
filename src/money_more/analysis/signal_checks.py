"""验证信号机械校验（Tier 1）：把 verify_signals 自由文本映射到可计算指标。

只接有稳定来源的指标：美债10Y、两市日均成交额、两融 5 日变化、PMI、板块资金净流入。
识别不了的一律标 narrative（需叙事判断），不硬猜；指标本轮缺失标 unknown。

语义边界：signal_checks 回答「条件达成了吗」（条件维度），价格 verdict 回答
「价格走对了吗」（结果维度）。两者并列呈现，不合并成分数——
watch 规避成功 + 条件未达成 = 一致；条件已达成而仍 pending = 值得人工看一眼。
"""

from __future__ import annotations

import re
from typing import Any

# —— 信号 → 指标解析（保守：模式不明确就返回 None，走叙事）——

_GE_WORDS = r"站稳|突破|以上|升至|达到|回升至|超|高于|站上"
_LE_WORDS = r"以下|以内|跌破|低于|降至|回落至|萎缩至|下破"


def _num_with_unit(text: str) -> tuple[float, str] | None:
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(万亿|亿|%|％)", text)
    if not m:
        return None
    return float(m.group(1)), m.group(2)


def parse_verify_signal(text: str) -> dict[str, Any] | None:
    """把一条验证信号解析成 {indicator, op, threshold}；解析不出返回 None。"""
    t = " ".join(str(text or "").split())
    if not t:
        return None

    # 美债 10Y：「美债10Y回落至4.2%以下」「美债10Y站稳4.8%」
    if "美债" in t and re.search(r"10\s*[Y年]", t, re.IGNORECASE):
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*[%％]", t)
        if not m:
            return None
        thr = float(m.group(1))
        if re.search(_LE_WORDS, t):
            return {"indicator": "us10y", "op": "<=", "threshold": thr, "unit": "%"}
        if re.search(_GE_WORDS, t):
            return {"indicator": "us10y", "op": ">=", "threshold": thr, "unit": "%"}
        return None

    # 市场日均成交额：「日均成交额站稳9500亿」「成交额突破1.5万亿」
    if "成交额" in t and re.search(r"万亿|亿", t):
        parsed = _num_with_unit(t)
        if not parsed:
            return None
        v, unit = parsed
        thr = v * (1e12 if unit == "万亿" else 1e8)
        if re.search(_LE_WORDS, t):
            return {"indicator": "market_amount", "op": "<=", "threshold": thr, "unit": "元"}
        if re.search(_GE_WORDS, t):
            return {"indicator": "market_amount", "op": ">=", "threshold": thr, "unit": "元"}
        return None

    # 两融/融资余额：方向型（相对 5 日变化率）
    if re.search(r"两融|融资余额", t):
        if re.search(r"回升|止跌|转正|增长|回流|攀升", t):
            return {"indicator": "margin_5d", "op": ">=", "threshold": 0.0, "unit": "%"}
        if re.search(r"收缩|下降|流出|回落|走低", t):
            return {"indicator": "margin_5d", "op": "<=", "threshold": 0.0, "unit": "%"}
        return None

    # PMI：荣枯线/环比
    if "PMI" in t.upper():
        if re.search(r"荣枯|扩张|站上\s*50|重回\s*50|50\s*以上", t):
            return {"indicator": "pmi", "op": ">=", "threshold": 50.0, "unit": ""}
        if re.search(r"回升|改善|反弹|走高", t):
            return {"indicator": "pmi_mom", "op": ">=", "threshold": 0.0, "unit": "pt"}
        return None

    # 板块资金：「主力资金净流入」「板块资金回流」（用建议所属板块比对当期资金流表）
    if re.search(r"主力|资金", t) and re.search(r"净流入|回流|流入|净买入", t):
        return {"indicator": "sector_flow", "op": ">=", "threshold": 0.0, "unit": "元"}

    return None


# —— 指标快照：全部来自既有数据，不接新源 ——


def build_signal_context(
    *,
    macro_raw: dict[str, Any] | None,
    turnover: dict[str, Any] | None,
) -> dict[str, Any]:
    """汇总可计算指标当前值。缺失为 None，由 check_signal 标 unknown。"""
    macro_raw = macro_raw or {}
    gl = macro_raw.get("global_liquidity") or {}
    margin = macro_raw.get("margin_trend") or {}
    mh = macro_raw.get("macro_hard") or {}
    flow = macro_raw.get("sector_money_flow") or {}
    turnover = turnover or {}

    pmi_rows = [r for r in (mh.get("pmi") or []) if isinstance(r, dict)]
    pmi_latest = pmi_rows[0].get("value") if pmi_rows else None
    pmi_prev = pmi_rows[1].get("value") if len(pmi_rows) > 1 else None

    sector_net: dict[str, float] = {}
    for key in ("rank_by_inflow", "top_inflow", "top_losers"):
        for row in flow.get(key) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("板块") or row.get("名称") or "").strip()
            try:
                net = float(row.get("净流入"))
            except (TypeError, ValueError):
                continue
            if name and name not in sector_net:
                sector_net[name] = net

    return {
        "us10y": (gl.get("us_10y") or {}).get("latest"),
        "margin_5d_pct": margin.get("financing_balance_change_5d_pct"),
        "pmi": pmi_latest,
        "pmi_prev": pmi_prev,
        "market_amount_avg20": turnover.get("avg20"),
        "market_amount_today": turnover.get("latest"),
        "market_amount_as_of": turnover.get("as_of"),
        "sector_net_inflow": sector_net,
        "turnover_errors": list(turnover.get("errors") or []),
    }


def _measure(
    spec: dict[str, Any], ctx: dict[str, Any], sector: str | None
) -> tuple[float | None, str]:
    """返回 (measured, 展示用 detail)。None = 指标缺失。"""
    ind = spec["indicator"]
    if ind == "us10y":
        v = ctx.get("us10y")
        return (float(v), f"美债10Y 现 {v}%") if v is not None else (None, "美债10Y 本轮缺失")
    if ind == "market_amount":
        v = ctx.get("market_amount_avg20") or ctx.get("market_amount_today")
        if v is None:
            return None, "两市成交额本轮缺失"
        yi = v / 1e8
        basis = "20日均" if ctx.get("market_amount_avg20") else "当日"
        return float(v), f"两市成交额{basis} {yi:,.0f} 亿"
    if ind == "margin_5d":
        v = ctx.get("margin_5d_pct")
        return (
            (float(v), f"融资余额近5日变化 {v}%")
            if v is not None
            else (None, "两融趋势本轮缺失")
        )
    if ind == "pmi":
        v = ctx.get("pmi")
        return (float(v), f"制造业PMI 现 {v}") if v is not None else (None, "PMI 本轮缺失")
    if ind == "pmi_mom":
        cur, prev = ctx.get("pmi"), ctx.get("pmi_prev")
        if cur is None or prev is None:
            return None, "PMI 环比基期缺失"
        delta = round(float(cur) - float(prev), 2)
        return delta, f"制造业PMI 环比 {delta:+}（{prev}→{cur}）"
    if ind == "sector_flow":
        if not sector:
            return None, "建议无板块归属，无法比对资金流"
        nets: dict[str, float] = ctx.get("sector_net_inflow") or {}
        for name, net in nets.items():
            if sector in name or name in sector:
                return net, f"板块[{name}]主力净流入 {net / 1e8:+.1f} 亿"
        return None, f"板块[{sector}]不在当期资金流表内"
    return None, "未知指标"


def check_signal(
    text: str,
    ctx: dict[str, Any],
    *,
    sector: str | None = None,
) -> dict[str, Any]:
    """校验一条信号。verdict: met / unmet / unknown / narrative。"""
    spec = parse_verify_signal(text)
    if not spec:
        return {"text": str(text or ""), "checkable": False, "verdict": "narrative"}
    measured, detail = _measure(spec, ctx, sector)
    out = {
        "text": str(text or ""),
        "checkable": True,
        "indicator": spec["indicator"],
        "op": spec["op"],
        "threshold": spec["threshold"],
        "detail": detail,
    }
    if measured is None:
        out["verdict"] = "unknown"
        return out
    out["measured"] = round(float(measured), 2)
    ok = measured >= spec["threshold"] if spec["op"] == ">=" else measured <= spec["threshold"]
    out["verdict"] = "met" if ok else "unmet"
    return out


def summarize_signal_checks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """台账级汇总：多少信号可机查、达成/未达成/需叙事。"""
    checks = [c for r in rows or [] for c in (r.get("signal_checks") or [])]
    return {
        "total": len(checks),
        "checkable": sum(1 for c in checks if c.get("checkable")),
        "met": sum(1 for c in checks if c.get("verdict") == "met"),
        "unmet": sum(1 for c in checks if c.get("verdict") == "unmet"),
        "unknown": sum(1 for c in checks if c.get("verdict") == "unknown"),
        "narrative": sum(1 for c in checks if c.get("verdict") == "narrative"),
    }
