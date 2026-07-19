"""市场微观结构 / 流动性断点（规则层）。

回答：常规「基本面→价格」传导是否仍大致可用，还是进入拥挤共振/流动性压力状态。
不指控「量化有罪」，只给可核对的市场结构信号。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from money_more.data.fetcher import _safe_float


def assess_market_microstructure(
    overview: dict[str, Any] | None,
    spot: pd.DataFrame | None = None,
) -> dict[str, Any]:
    overview = overview or {}
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
        # 单位常为亿元；大幅净流出粗阈值
        if nb_net <= -80:
            flags.append(f"北向净流出偏大({nb_net})")

    regime = _classify_regime(flags, metrics)
    fundamental_channel_ok = regime in ("normal", "elevated")
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
        fundamental_channel_ok = True
    else:
        implication = "未见显著拥挤共振或流动性断点；常规中长线分析框架仍大致适用。"

    return {
        "regime": regime,
        "fundamental_channel_ok": fundamental_channel_ok,
        "flags": flags,
        "metrics": metrics,
        "implication": implication,
        "plain_note": (
            f"微观结构状态=`{regime}`；"
            + ("基本面→价格传导可能受扰。" if not fundamental_channel_ok else "传导大致可用。")
        ),
        "layer": "mechanism",  # 机制层：偏主结论可用的硬信号，但仍单独成块
    }


def _spot_sync_metrics(spot: pd.DataFrame | None) -> dict[str, Any]:
    if spot is None or not isinstance(spot, pd.DataFrame) or spot.empty:
        return {}
    df = spot.copy()
    # 列名兼容
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
    if sync and stress:
        return "liquidity_stress"
    if sync:
        return "crowded_sync"
    if stress and len(flags) >= 2:
        return "liquidity_stress"
    if flags:
        return "elevated"
    # 无 flag 时也可由指标轻判
    down_ratio = metrics.get("down_ratio") or 0
    up_ratio = metrics.get("up_ratio") or 0
    if max(down_ratio, up_ratio) >= 0.7:
        return "crowded_sync"
    return "normal"
