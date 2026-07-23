"""决策后处理：仓位/止损硬约束 + 数据质量降级，不信任 LLM 裸输出。"""

from __future__ import annotations

from typing import Any


def validate_recommendations(
    recommendations: list[dict[str, Any]],
    *,
    holdings: list[dict[str, Any]],
    constraints: dict[str, float],
    quotes: dict[str, float | None] | None = None,
    data_quality: dict[str, Any] | None = None,
    market_risk_level: str | None = None,
    hard_gates: dict[str, dict[str, Any]] | None = None,
    quotes_meta: dict[str, dict[str, Any]] | None = None,
    allowed_codes: set[str] | list[str] | None = None,
    info_completeness: dict[str, dict[str, Any]] | None = None,
    microstructure: dict[str, Any] | None = None,
    earnings_revisions: dict[str, dict[str, Any]] | None = None,
    global_liquidity: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """返回 (修正后的建议列表, 覆盖说明)。"""
    overrides: list[str] = []
    quotes = quotes or {}
    quotes_meta = quotes_meta or {}
    hard_gates = hard_gates or {}
    dq = data_quality or {}
    max_single = float(constraints.get("max_single_position_pct", 20))
    max_total = float(constraints.get("max_total_position_pct", 80))
    stop_loss_pct = float(constraints.get("stop_loss_pct", 8))
    take_profit_pct = float(constraints.get("take_profit_pct", 25))

    # 数据质量 / 风险环境 → 收紧总仓位
    score = float(dq.get("score") or 1.0)
    regime_mult = 1.0
    if score < 0.4:
        regime_mult *= 0.4
        overrides.append(f"data_quality={score}<0.4 → 总仓位×0.4，禁止新开仓")
    elif score < 0.6:
        regime_mult *= 0.7
        overrides.append(f"data_quality={score}<0.6 → 总仓位×0.7")

    risk = (market_risk_level or "").lower()
    if risk in ("high", "极高", "高", "aggressive"):
        regime_mult *= 0.6
        overrides.append(f"market_risk={market_risk_level} → 总仓位×0.6")
    elif risk in ("elevated", "偏高", "中高"):
        regime_mult *= 0.8
        overrides.append(f"market_risk={market_risk_level} → 总仓位×0.8")

    # 微观结构断点：收紧总仓、抑制新开仓（机制层信号）
    micro = microstructure or {}
    micro_regime = str(micro.get("regime") or "normal")
    if micro_regime == "liquidity_stress":
        regime_mult *= 0.7
        overrides.append("microstructure=liquidity_stress → 总仓位×0.7，抑制新开仓")
    elif micro_regime == "crowded_sync":
        regime_mult *= 0.85
        overrides.append("microstructure=crowded_sync → 总仓位×0.85")

    gl = global_liquidity or {}
    gl_stance = str(gl.get("stance") or "unknown")
    if gl_stance == "tightening":
        regime_mult *= 0.85
        overrides.append("global_liquidity=tightening → 总仓位×0.85")

    effective_max_total = max_total * regime_mult
    forbid_new_buys = score < 0.4 or micro_regime == "liquidity_stress"
    info_map = info_completeness or {}
    earn_map = earnings_revisions or {}

    holding_by_code = {
        "".join(ch for ch in str(h.get("code") or "") if ch.isdigit())[-6:].zfill(6): h
        for h in holdings
        if h.get("code")
    }
    is_empty_book = len(holding_by_code) == 0
    allow: set[str] | None = None
    if allowed_codes is not None:
        allow = {
            "".join(ch for ch in str(c) if ch.isdigit())[-6:].zfill(6)
            for c in allowed_codes
            if c
        }
        allow |= set(holding_by_code.keys())
    out: list[dict[str, Any]] = []

    for raw in recommendations:
        rec = dict(raw)
        code = str(rec.get("code") or "").zfill(6)[-6:]
        code = "".join(ch for ch in code if ch.isdigit())[-6:].zfill(6)
        rec["code"] = code
        action = str(rec.get("action") or "watch").lower().strip()
        if action in ("买入", "增持"):
            action = "buy"
        elif action in ("卖出", "减持"):
            action = "sell"
        elif action in ("持有",):
            action = "hold"
        elif action in ("观望", "观察"):
            action = "watch"
        rec["action"] = action

        # 空仓硬校验：禁止 hold/sell/add（无真实持仓可操作）
        if is_empty_book and action in ("hold", "sell", "add"):
            new_act = "buy" if action == "add" else "watch"
            overrides.append(f"{code}: 空仓禁止 {action}→{new_act}")
            action = new_act
            rec["action"] = new_act
            if new_act == "watch":
                rec["position_pct"] = 0.0

        # 深度池白名单：池外代码不得 buy/add
        if allow is not None and code not in allow and action in ("buy", "add"):
            overrides.append(f"{code}: 不在深度池/声明持仓 → watch")
            action = "watch"
            rec["action"] = "watch"
            rec["position_pct"] = 0.0

        gate = hard_gates.get(code) or {}
        if (gate.get("block_buy") or gate.get("force_watch")) and action in ("buy", "add"):
            overrides.append(f"{code}: 硬门禁 → watch ({'; '.join(gate.get('reasons') or [])})")
            action = "watch"
            rec["action"] = "watch"
            rec["position_pct"] = 0.0

        # 信息完备性：公开信息不足以解释异动 → 观望（非指控内幕）
        info = info_map.get(code) or {}
        if info.get("status") == "gap_suspected" and action in ("buy", "add"):
            sev = str(info.get("severity") or "low")
            if sev in ("high", "medium") or info.get("action_hint") == "watch":
                overrides.append(
                    f"{code}: 信息缺口({sev}) → watch | "
                    + "; ".join(info.get("unexplained") or info.get("reasons") or [])[:80]
                )
                action = "watch"
                rec["action"] = "watch"
                rec["position_pct"] = 0.0
                rec["info_completeness"] = info

        earn = earn_map.get(code) or {}
        if earn.get("signal") == "negative" and action in ("buy", "add"):
            overrides.append(
                f"{code}: 盈利预期下修 → watch | "
                + "; ".join(earn.get("evidence") or [])[:80]
            )
            action = "watch"
            rec["action"] = "watch"
            rec["position_pct"] = 0.0
            rec["earnings_revision"] = earn

        conf = rec.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else 0.5
        except (TypeError, ValueError):
            conf_f = 0.5
        conf_f = max(0.0, min(1.0, conf_f))
        if conf is not None and abs(conf_f - float(conf)) > 1e-9:
            overrides.append(f"{code}: confidence clamp {conf}→{conf_f}")
        try:
            ih = float(info.get("confidence_haircut") or 0)
            if ih > 0:
                conf_f = max(0.05, conf_f - ih)
                overrides.append(f"{code}: 信息完备性 haircut -{ih}")
        except (TypeError, ValueError):
            pass
        rec["confidence"] = conf_f

        pos = rec.get("position_pct")
        try:
            pos_f = float(pos) if pos is not None else 0.0
        except (TypeError, ValueError):
            pos_f = 0.0

        if action in ("buy", "hold", "add"):
            if forbid_new_buys and action == "buy" and code not in holding_by_code:
                if micro_regime == "liquidity_stress":
                    overrides.append(f"{code}: 微观结构liquidity_stress禁止新买 → watch")
                elif score < 0.4:
                    overrides.append(f"{code}: 数据质量过低禁止新买 → watch")
                else:
                    overrides.append(f"{code}: 风控禁止新买 → watch")
                action = "watch"
                rec["action"] = "watch"
                pos_f = 0.0
            # 置信度缩放仓位
            sized = pos_f * (0.5 + 0.5 * conf_f)
            # ATR% 波动率缩放：波动越高仓位越低
            atr = None
            try:
                atr = float((quotes_meta.get(code) or {}).get("atr_pct_20d")) if quotes_meta else None
            except (TypeError, ValueError, AttributeError):
                atr = None
            if atr is None:
                # quotes 可能只是价格；允许从 rec 附带
                try:
                    atr = float(rec.get("atr_pct_20d")) if rec.get("atr_pct_20d") is not None else None
                except (TypeError, ValueError):
                    atr = None
            if atr and atr > 0:
                # 基准 3% ATR → 满仓系数 1；ATR 6% → 0.5
                vol_scale = min(1.2, max(0.35, 3.0 / atr))
                if abs(vol_scale - 1.0) > 0.05:
                    overrides.append(f"{code}: ATR%{atr} → 仓位×{vol_scale:.2f}")
                sized *= vol_scale
            if sized > max_single:
                overrides.append(f"{code}: position {sized:.1f}%→{max_single}% (max_single)")
                sized = max_single
            pos_f = max(0.0, sized)
        else:
            pos_f = 0.0 if action in ("sell", "watch") else max(0.0, min(pos_f, max_single))

        rec["position_pct"] = round(pos_f, 2)

        # 止损/止盈相对成本或现价
        holding = holding_by_code.get(code)
        px = quotes.get(code)
        cost = None
        if holding:
            try:
                cost = float(holding.get("cost"))
            except (TypeError, ValueError):
                cost = None
        ref = cost if cost and cost > 0 else (float(px) if px else None)

        if ref and action in ("buy", "hold", "add"):
            min_stop = round(ref * (1 - stop_loss_pct / 100), 4)
            max_target = round(ref * (1 + take_profit_pct / 100 * 2), 4)  # 允许略超配置止盈
            stop = rec.get("stop_loss")
            try:
                stop_f = float(stop) if stop is not None else min_stop
            except (TypeError, ValueError):
                stop_f = min_stop
            if stop_f < min_stop * 0.98:  # 止损过宽
                overrides.append(f"{code}: stop_loss {stop_f}→{min_stop} (max {stop_loss_pct}%)")
                stop_f = min_stop
            if stop_f >= ref:
                overrides.append(f"{code}: stop_loss >= ref → {min_stop}")
                stop_f = min_stop
            rec["stop_loss"] = stop_f

            tgt = rec.get("target_price")
            try:
                tgt_f = float(tgt) if tgt is not None else round(ref * (1 + take_profit_pct / 100), 4)
            except (TypeError, ValueError):
                tgt_f = round(ref * (1 + take_profit_pct / 100), 4)
            if tgt_f <= ref:
                tgt_f = round(ref * (1 + take_profit_pct / 100), 4)
                overrides.append(f"{code}: target_price 过低 → {tgt_f}")
            if tgt_f > max_target:
                overrides.append(f"{code}: target_price {tgt_f}→{max_target}")
                tgt_f = max_target
            rec["target_price"] = tgt_f

        # 已持仓却标 buy → 改为 add/hold
        if code in holding_by_code and action == "buy":
            rec["action"] = "add"
            overrides.append(f"{code}: 已持仓 buy→add")

        rec.setdefault("validation", {})
        rec["validation"] = {
            "regime_mult": regime_mult,
            "effective_max_total": effective_max_total,
            "data_quality_score": score,
        }
        out.append(rec)

    # 确保每个持仓都有建议
    present = {r["code"] for r in out}
    for code, h in holding_by_code.items():
        if code not in present:
            ref = float(h.get("cost") or 0) or quotes.get(code)
            out.append(
                {
                    "code": code,
                    "action": "hold",
                    "confidence": 0.4,
                    "position_pct": 0.0,
                    "rationale": "系统补全：持仓未出现在 LLM 建议中，默认 hold",
                    "stop_loss": round(float(ref) * (1 - stop_loss_pct / 100), 4) if ref else None,
                    "target_price": round(float(ref) * (1 + take_profit_pct / 100), 4) if ref else None,
                    "validation": {"auto_filled": True},
                }
            )
            overrides.append(f"{code}: 补全缺失持仓建议 → hold")

    # 总仓位缩放
    deployable = [
        r
        for r in out
        if str(r.get("action")).lower() in ("buy", "hold", "add") and float(r.get("position_pct") or 0) > 0
    ]
    total = sum(float(r.get("position_pct") or 0) for r in deployable)
    if total > effective_max_total and total > 0:
        scale = effective_max_total / total
        overrides.append(f"总仓位 {total:.1f}%→{effective_max_total:.1f}% (scale={scale:.2f})")
        for r in deployable:
            r["position_pct"] = round(float(r["position_pct"]) * scale, 2)

    # 行业集中度：同一 sector_tag 合计不超过 max_single*1.5
    sector_cap = max_single * 1.5
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for r in out:
        if str(r.get("action")).lower() not in ("buy", "hold", "add"):
            continue
        if float(r.get("position_pct") or 0) <= 0:
            continue
        tag = str(r.get("sector_tag") or r.get("sector") or "unknown")
        by_sector.setdefault(tag, []).append(r)
    for tag, rows in by_sector.items():
        if tag == "unknown":
            continue
        ssum = sum(float(r.get("position_pct") or 0) for r in rows)
        if ssum > sector_cap and ssum > 0:
            scale = sector_cap / ssum
            overrides.append(f"板块[{tag}]仓位 {ssum:.1f}%→{sector_cap:.1f}%")
            for r in rows:
                r["position_pct"] = round(float(r["position_pct"]) * scale, 2)

    if overrides:
        for r in out:
            r.setdefault("validation", {})
            r["validation"]["overrides"] = [o for o in overrides if r["code"] in o or o.startswith("总") or o.startswith("data") or o.startswith("market") or o.startswith("板块")]

    return out, overrides


def enrich_holdings(
    holdings: list[Any],
    quotes: dict[str, float | None],
) -> list[dict[str, Any]]:
    """为持仓补充市值/浮盈，供决策 LLM 使用。"""
    enriched: list[dict[str, Any]] = []
    total_mv = 0.0
    rows: list[dict[str, Any]] = []
    for h in holdings:
        code = getattr(h, "code", None) or h.get("code")  # type: ignore[union-attr]
        qty = float(getattr(h, "quantity", None) or h.get("quantity") or 0)  # type: ignore[union-attr]
        cost = float(getattr(h, "cost", None) or h.get("cost") or 0)  # type: ignore[union-attr]
        px = quotes.get(str(code))
        mv = (px or cost) * qty if qty else 0.0
        pnl_pct = None
        if px and cost:
            pnl_pct = round((px - cost) / cost * 100, 2)
        row = {
            "code": str(code),
            "quantity": qty,
            "cost": cost,
            "current_price": px,
            "market_value": round(mv, 2),
            "unrealized_pnl_pct": pnl_pct,
        }
        rows.append(row)
        total_mv += mv
    for row in rows:
        row["weight_pct"] = round(row["market_value"] / total_mv * 100, 2) if total_mv else 0.0
        enriched.append(row)
    return enriched
