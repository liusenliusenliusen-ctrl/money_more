"""经营现金流质量：中长线防「纸面富贵」闸门。"""

from __future__ import annotations

from typing import Any


def assess_ocf_quality(
    ts_bundle: dict[str, Any] | None,
    *,
    min_ocf_to_profit: float = 0.5,
    require_periods: int = 2,
    block_on_negative_ocf: bool = True,
) -> dict[str, Any]:
    """评估经营现金流相对净利润的质量。

    优先用 fina_indicator.ocf_to_profit；否则用 cashflow.n_cashflow_act / income 净利润推算。
    """
    bundle = ts_bundle or {}
    fina = (bundle.get("financials") or {}).get("indicators") or []
    cashflow = (bundle.get("financials") or {}).get("cashflow") or []
    income = (bundle.get("financials") or {}).get("income") or []

    ratios = _ratios_from_fina(fina)
    source = "fina_indicator" if ratios else "none"
    if not ratios:
        ratios = _ratios_from_statements(cashflow, income)
        source = "cashflow/income" if ratios else "none"

    evidence: list[str] = []
    if not ratios:
        return {
            "signal": "unknown",
            "ocf_to_profit": None,
            "ocf_to_profit_avg": None,
            "periods": 0,
            "ni_ocf_divergence": False,
            "block_buy": False,
            "force_watch": False,
            "evidence": ["经营现金流数据不足"],
            "data_source": source,
            "note": "无可用 OCF/净利润比，跳过现金流硬闸",
        }

    avg = sum(ratios) / len(ratios)
    neg_with_pos_ni = _count_neg_ocf_with_pos_ni(cashflow, income, fina)
    divergence = neg_with_pos_ni >= max(1, int(require_periods))

    evidence.append(f"OCF/净利润近{len(ratios)}期均值={avg:.2f}")
    if divergence:
        evidence.append(f"连续/多期利润为正但经营现金流为负（{neg_with_pos_ni}期）")

    signal = "adequate"
    block_buy = False
    force_watch = False
    if divergence and block_on_negative_ocf:
        signal = "weak"
        block_buy = True
        force_watch = True
        evidence.append("纸面富贵风险 → 禁止新买")
    elif avg < 0:
        signal = "weak"
        force_watch = True
        if block_on_negative_ocf:
            block_buy = True
        evidence.append("经营现金流相对利润持续为负")
    elif avg < float(min_ocf_to_profit):
        signal = "weak"
        force_watch = True
        evidence.append(f"OCF/净利润 < {min_ocf_to_profit}（质量偏弱）")
    elif avg >= 0.8:
        signal = "strong"
        evidence.append("经营现金流对利润覆盖良好")
    else:
        signal = "adequate"

    return {
        "signal": signal,
        "ocf_to_profit": round(ratios[0], 3),
        "ocf_to_profit_avg": round(avg, 3),
        "periods": len(ratios),
        "ni_ocf_divergence": divergence,
        "block_buy": block_buy,
        "force_watch": force_watch,
        "evidence": evidence,
        "data_source": source,
        "note": "；".join(evidence[:3]),
    }


def _ratios_from_fina(fina: list[Any]) -> list[float]:
    out: list[float] = []
    for row in fina[:4]:
        if not isinstance(row, dict):
            continue
        r = _f(row.get("ocf_to_profit"))
        if r is not None:
            out.append(r)
    return out


def _ratios_from_statements(cashflow: list[Any], income: list[Any]) -> list[float]:
    out: list[float] = []
    n = min(len(cashflow), len(income), 4)
    for i in range(n):
        cf = cashflow[i] if isinstance(cashflow[i], dict) else {}
        inc = income[i] if isinstance(income[i], dict) else {}
        ocf = _f(
            cf.get("n_cashflow_act")
            or cf.get("n_cash_flows_act")
            or cf.get("net_operate_cash_flow")
        )
        ni = _f(
            inc.get("n_income")
            or inc.get("n_income_attr_p")
            or inc.get("net_profit")
            or cf.get("net_profit")
        )
        if ocf is None or ni is None or abs(ni) < 1e-6:
            continue
        out.append(ocf / ni)
    return out


def _count_neg_ocf_with_pos_ni(
    cashflow: list[Any],
    income: list[Any],
    fina: list[Any],
) -> int:
    """统计「净利润>0 且 经营现金流<0」的期数。"""
    count = 0
    # 优先报表逐期对齐
    n = min(len(cashflow), max(len(income), 1), 4)
    if cashflow and income:
        for i in range(n):
            cf = cashflow[i] if isinstance(cashflow[i], dict) else {}
            inc = income[i] if i < len(income) and isinstance(income[i], dict) else {}
            ocf = _f(cf.get("n_cashflow_act") or cf.get("n_cash_flows_act"))
            ni = _f(inc.get("n_income") or inc.get("n_income_attr_p") or inc.get("net_profit"))
            if ni is not None and ni > 0 and ocf is not None and ocf < 0:
                count += 1
        return count
    # 回退：ocf_to_profit < 0 且能从 fina 看到正 ROE/利润倾向
    for row in fina[:4]:
        if not isinstance(row, dict):
            continue
        r = _f(row.get("ocf_to_profit"))
        roe = _f(row.get("roe") or row.get("roe_waa"))
        if r is not None and r < 0 and (roe is None or roe > 0):
            count += 1
    return count


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
