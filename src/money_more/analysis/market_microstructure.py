"""市场微观结构 / 流动性断点（规则层）。

回答：常规「基本面→价格」传导是否仍大致可用，还是进入拥挤共振/流动性压力状态。
不指控「量化有罪」，只给可核对的市场结构信号。

波次门禁（optimization-plan-v2）：
- 重度：单日禁新买
- 轻/中度：跨轮确认后再禁
- 极端涨跌停比：升格约束
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from money_more.config import MicrostructureConfig
from money_more.data.fetcher import _safe_float


def assess_market_microstructure(
    overview: dict[str, Any] | None,
    spot: pd.DataFrame | None = None,
    *,
    config: MicrostructureConfig | None = None,
    prior_micro: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overview = overview or {}
    cfg = config or MicrostructureConfig()
    metrics: dict[str, Any] = {}
    flags: list[str] = []

    limit_up = overview.get("limit_up_count")
    limit_down = overview.get("limit_down_count")
    if limit_up is not None:
        metrics["limit_up_count"] = int(limit_up)
    if limit_down is not None:
        metrics["limit_down_count"] = int(limit_down)
    if isinstance(limit_down, int) and limit_down >= 40:
        flags.append(f"跌停家数偏多({limit_down})")
    if isinstance(limit_up, int) and isinstance(limit_down, int) and limit_up + limit_down >= 80:
        flags.append(f"涨跌停合计偏多({limit_up}+{limit_down})")

    # 极端扩散/拥挤：涨停远多于跌停（如 103:1）
    extreme_crowding = False
    if isinstance(limit_up, int) and limit_up >= 40:
        denom = max(int(limit_down or 0), 1)
        ratio = limit_up / denom
        metrics["limit_up_down_ratio"] = round(ratio, 2)
        if ratio >= float(cfg.extreme_limit_ratio) or (
            limit_up >= 80 and int(limit_down or 0) <= 2
        ):
            extreme_crowding = True
            flags.append(f"涨跌停比极端拥挤({limit_up}:{limit_down})")

    # 指数单日波动
    idx_moves: list[float] = []
    for item in overview.get("indices") or []:
        chg = _safe_float(item.get("change_pct"))
        if chg is not None:
            idx_moves.append(abs(chg))
    if idx_moves:
        metrics["index_abs_change_max"] = round(max(idx_moves), 2)
        metrics["index_abs_change_avg"] = round(sum(idx_moves) / len(idx_moves), 2)
        if max(idx_moves) >= 2.5:
            flags.append(f"主要指数波动偏大(max|{max(idx_moves):.1f}%|)")

    spot_metrics = _spot_sync_metrics(spot)
    metrics.update(spot_metrics)
    up_ratio = spot_metrics.get("up_ratio")
    down_ratio = spot_metrics.get("down_ratio")
    if up_ratio is not None and up_ratio >= 0.75:
        flags.append(f"同向性偏强：上涨家数占比 {up_ratio:.0%}")
    if down_ratio is not None and down_ratio >= 0.75:
        flags.append(f"同向性偏强：下跌家数占比 {down_ratio:.0%}")

    top_share = spot_metrics.get("amount_top50_share")
    if top_share is not None and top_share >= 0.45:
        flags.append(f"成交额高度集中：前50占比 {top_share:.0%}")

    median_abs = spot_metrics.get("median_abs_change_pct")
    if median_abs is not None and median_abs >= 3.5:
        flags.append(f"个股中位波动偏大({median_abs}%)")

    # 北向大幅净流出（若有）
    nb = overview.get("northbound") or {}
    nb_net = _safe_float(nb.get("latest_net"))
    if nb_net is not None:
        metrics["northbound_latest_net"] = nb_net
        if nb_net <= -80:
            flags.append(f"北向净流出偏大({nb_net})")

    regime = _classify_regime(flags, metrics)
    severity, stress_extra = _classify_severity(regime, flags, extreme_crowding)
    prior = prior_micro or {}
    prior_sev = str(prior.get("severity") or "")
    prior_pending = bool(prior.get("pending_confirm"))
    prior_pressure = prior_sev in ("mild", "moderate", "severe") or str(
        prior.get("regime") or ""
    ) in ("crowded_sync", "liquidity_stress", "elevated")

    forbid_new_buys = False
    pending_confirm = False
    confirm_note = ""

    if severity == "severe" and cfg.severe_forbid_new_buys:
        forbid_new_buys = True
        confirm_note = "重度机制压力：本轮立即禁新开仓"
    elif severity in ("mild", "moderate"):
        pending_confirm = True
        need = max(1, int(cfg.mild_confirm_rounds))
        if need <= 1 and prior_pressure:
            forbid_new_buys = True
            confirm_note = f"{severity}压力已跨轮确认：禁新开仓"
        else:
            confirm_note = f"{severity}压力观察中：本轮降权不全面禁买"
    elif prior_pending and severity == "none" and regime == "normal":
        confirm_note = "结构压力已解除"

    if extreme_crowding and not forbid_new_buys:
        # 极端拥挤至少压进攻；若已是 moderate 以上在上面处理
        if severity in ("mild", "moderate", "severe"):
            forbid_new_buys = True
            confirm_note = (confirm_note + "；" if confirm_note else "") + "极端涨跌停比→禁新买"

    fundamental_channel_ok = regime in ("normal", "elevated") and severity in ("none", "mild")
    if regime == "crowded_sync":
        implication = (
            "价格同向性高，短线量价/个股分化规律变弱；中长线可继续看基本面，"
            "但应降低对「逻辑对就立刻兑现」的预期，新开仓更保守。"
        )
        fundamental_channel_ok = False
    elif regime == "liquidity_stress":
        implication = (
            "流动性/波动压力上升，常规估值修复可能失效或延迟；优先现金与高流动性标的，"
            "避免在断点期左侧加仓。"
        )
        fundamental_channel_ok = False
    elif regime == "elevated":
        implication = "微观结构略紧，主结论仍可用，但对追涨与拥挤赛道提高警惕。"
        fundamental_channel_ok = severity == "none"
    else:
        implication = "未见显著拥挤共振或流动性断点；常规中长线分析框架仍大致适用。"

    if extreme_crowding:
        implication += " 涨跌停比极端，属拥挤扩散，不宜追涨式加仓。"

    # 升乐观双确认：未回到 normal 且传导可用 → 禁新开仓（elevated 不得一日转买）
    if regime != "normal" or not fundamental_channel_ok:
        if not forbid_new_buys:
            forbid_new_buys = True
            confirm_note = (
                (confirm_note + "；" if confirm_note else "")
                + "微观未回到normal/传导可用：禁新开仓"
            )

    regime_mult = {"none": 1.0, "mild": 0.85, "moderate": 0.85, "severe": 0.7}.get(
        severity, 1.0
    )

    return {
        "regime": regime,
        "severity": severity,
        "severity_extra_flags": stress_extra,
        "extreme_crowding": extreme_crowding,
        "pending_confirm": pending_confirm,
        "forbid_new_buys": forbid_new_buys,
        "prior_severity": prior_sev or None,
        "regime_position_mult": regime_mult,
        "fundamental_channel_ok": fundamental_channel_ok,
        "flags": flags,
        "metrics": metrics,
        "implication": implication,
        "confirm_note": confirm_note,
        "plain_note": (
            f"微观结构状态=`{regime}`/severity=`{severity}`；"
            + ("禁新开仓。" if forbid_new_buys else ("观察中。" if pending_confirm else ""))
            + ("基本面→价格传导可能受扰。" if not fundamental_channel_ok else "传导大致可用。")
            + (f" {confirm_note}" if confirm_note else "")
        ),
        "layer": "mechanism",
    }


def _spot_sync_metrics(spot: pd.DataFrame | None) -> dict[str, Any]:
    if spot is None or not isinstance(spot, pd.DataFrame) or spot.empty:
        return {}
    df = spot.copy()
    chg_col = "涨跌幅" if "涨跌幅" in df.columns else ("change_pct" if "change_pct" in df.columns else None)
    amt_col = "成交额" if "成交额" in df.columns else ("amount" if "amount" in df.columns else None)
    if not chg_col:
        return {}
    chg = pd.to_numeric(df[chg_col], errors="coerce").dropna()
    if chg.empty:
        return {}
    n = len(chg)
    up = int((chg > 0).sum())
    down = int((chg < 0).sum())
    out: dict[str, Any] = {
        "spot_sample_size": n,
        "up_count": up,
        "down_count": down,
        "up_ratio": round(up / n, 3) if n else None,
        "down_ratio": round(down / n, 3) if n else None,
        "median_abs_change_pct": round(float(chg.abs().median()), 2),
        "pct_abs_ge_5": round(float((chg.abs() >= 5).mean()), 3),
    }
    if amt_col and amt_col in df.columns:
        amt = pd.to_numeric(df[amt_col], errors="coerce").fillna(0)
        total = float(amt.sum())
        if total > 0:
            top = float(amt.nlargest(min(50, len(amt))).sum())
            out["amount_top50_share"] = round(top / total, 3)
    return out


def _classify_regime(flags: list[str], metrics: dict[str, Any]) -> str:
    sync = any("同向性" in f for f in flags)
    stress = any(k in f for f in flags for k in ("跌停", "波动偏大", "北向净流出", "成交额高度集中"))
    extreme = any("极端拥挤" in f for f in flags)
    if extreme and (sync or stress):
        return "liquidity_stress"
    if sync and stress:
        return "liquidity_stress"
    if sync:
        return "crowded_sync"
    if stress and len(flags) >= 2:
        return "liquidity_stress"
    if extreme:
        return "crowded_sync"
    if flags:
        return "elevated"
    down_ratio = metrics.get("down_ratio") or 0
    up_ratio = metrics.get("up_ratio") or 0
    if max(down_ratio, up_ratio) >= 0.7:
        return "crowded_sync"
    return "normal"


def _classify_severity(
    regime: str,
    flags: list[str],
    extreme_crowding: bool,
) -> tuple[str, int]:
    """返回 (severity, 额外硬 flag 数)。severity: none|mild|moderate|severe"""
    hard_keys = ("跌停", "波动偏大", "北向净流出", "成交额高度集中", "极端拥挤")
    extra = sum(1 for f in flags if any(k in f for k in hard_keys))
    sync = any("同向性" in f for f in flags)

    if regime == "liquidity_stress":
        if extreme_crowding or extra >= 2 or (sync and extra >= 1):
            return "severe", extra
        return "moderate", extra
    if regime == "crowded_sync":
        if extreme_crowding or extra >= 1:
            return "moderate", extra
        return "mild", extra
    if regime == "elevated":
        return "mild", extra
    if extreme_crowding:
        return "moderate", extra
    return "none", extra
