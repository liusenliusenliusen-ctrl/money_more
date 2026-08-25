"""决策后处理：仓位/止损硬约束 + 数据质量降级，不信任 LLM 裸输出。"""

from __future__ import annotations

from typing import Any

from money_more.analysis.wave2_enrich import (
    enrich_sector_link,
    enrich_verify_window,
    refresh_sector_link_rationale,
)


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
    ocf_quality: dict[str, dict[str, Any]] | None = None,
    equity_bond: dict[str, Any] | None = None,
    framework_gates: dict[str, Any] | None = None,
    sector_analyses: list[dict[str, Any]] | None = None,
    research_by_code: dict[str, Any] | None = None,
    verify_ledger: dict[str, Any] | None = None,
    macro_hard_meta: dict[str, Any] | None = None,
    margin_trend: dict[str, Any] | None = None,
    paper_holdings: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """返回 (修正后的建议列表, 覆盖说明)。"""
    overrides: list[str] = []
    quotes = quotes or {}
    quotes_meta = quotes_meta or {}
    hard_gates = hard_gates or {}
    dq = data_quality or {}
    fw = framework_gates or {}
    max_single = float(constraints.get("max_single_position_pct", 20))
    max_total = float(constraints.get("max_total_position_pct", 80))
    # 中长线默认与 TradingConfig 对齐（15/40）；字段语义为失效价带/观察目标，非短线止损
    stop_loss_pct = float(constraints.get("stop_loss_pct", 15))
    take_profit_pct = float(constraints.get("take_profit_pct", 40))
    max_theme_names = int(constraints.get("max_theme_names") or constraints.get("max_deep_per_theme") or 5)

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

    # 微观结构：severity 分档 + 跨轮确认后的 forbid_new_buys
    micro = microstructure or {}
    micro_regime = str(micro.get("regime") or "normal")
    severity = str(micro.get("severity") or "none")
    micro_mult = micro.get("regime_position_mult")
    try:
        if micro_mult is not None:
            regime_mult *= float(micro_mult)
            if float(micro_mult) < 0.999:
                overrides.append(
                    f"microstructure={micro_regime}/{severity} → 总仓位×{float(micro_mult):.2f}"
                )
        elif micro_regime == "liquidity_stress":
            regime_mult *= 0.7
            overrides.append("microstructure=liquidity_stress → 总仓位×0.7，抑制新开仓")
        elif micro_regime == "crowded_sync":
            regime_mult *= 0.85
            overrides.append("microstructure=crowded_sync → 总仓位×0.85")
    except (TypeError, ValueError):
        pass
    if micro.get("confirm_note"):
        overrides.append(f"microstructure: {micro.get('confirm_note')}")
    if fw.get("contradiction_active"):
        try:
            hair = float(fw.get("contradiction_haircut") or 0.8)
        except (TypeError, ValueError):
            hair = 0.8
        if hair < 0.999:
            regime_mult *= hair
            overrides.append(f"framework contradiction → 总仓/进攻×{hair}")

    gl = global_liquidity or {}
    gl_stance = str(gl.get("stance") or "unknown")
    if gl_stance == "tightening":
        regime_mult *= 0.85
        overrides.append("global_liquidity=tightening → 总仓位×0.85")

    # 双源冲突发丝：与矛盾分支同族，不新造叙事
    hard_meta = macro_hard_meta or {}
    conflict_keys = [
        k for k, m in hard_meta.items() if isinstance(m, dict) and m.get("agreement") == "conflict"
    ]
    if conflict_keys:
        regime_mult *= 0.95
        overrides.append(f"macro_hard_meta conflict={','.join(conflict_keys[:4])} → 总仓×0.95")
    mt = margin_trend or {}
    if str(mt.get("agreement") or "") == "conflict":
        regime_mult *= 0.95
        overrides.append("margin_trend agreement=conflict → 总仓×0.95")

    effective_max_total = max_total * regime_mult
    # 股债相对价值：硬封顶（可审计），再与 regime 收紧取小
    eb = equity_bond or gl.get("equity_bond") or {}
    if eb.get("ok") and eb.get("implied_max_total_pct") is not None:
        try:
            erp_cap = float(eb["implied_max_total_pct"])
            if erp_cap < effective_max_total - 1e-6:
                overrides.append(
                    f"equity_bond={eb.get('regime')} ERP={eb.get('erp_bp')}bp "
                    f"→ 总仓上限{erp_cap:.0f}%（原有效上限{effective_max_total:.0f}%）"
                )
                effective_max_total = erp_cap
            elif eb.get("regime") in ("attractive", "neutral", "cautious", "expensive"):
                overrides.append(
                    f"equity_bond={eb.get('regime')} ERP={eb.get('erp_bp')}bp "
                    f"→ 总仓上限维持≤{effective_max_total:.0f}%"
                )
        except (TypeError, ValueError):
            pass
    # 硬现金地板：implied_min_cash_pct 强制总仓 ≤ 100-min_cash
    if eb.get("ok") and eb.get("implied_min_cash_pct") is not None:
        try:
            min_cash = float(eb["implied_min_cash_pct"])
            cash_cap = max(0.0, 100.0 - min_cash)
            if cash_cap < effective_max_total - 1e-6:
                overrides.append(
                    f"现金地板 min_cash={min_cash:.0f}% → 总仓硬上限{cash_cap:.0f}%"
                )
                effective_max_total = cash_cap
        except (TypeError, ValueError):
            pass

    # verify → 先验
    v_priors = (verify_ledger or {}).get("priors") or {}
    forbid_verify_sectors = {
        str(s).strip() for s in (v_priors.get("forbid_sectors") or []) if str(s).strip()
    }
    verify_conf_mult = 1.0
    try:
        verify_conf_mult = float(v_priors.get("confidence_mult") or 1.0)
    except (TypeError, ValueError):
        verify_conf_mult = 1.0
    for note in v_priors.get("notes") or []:
        overrides.append(f"verify_prior: {note}")

    # 微观分档：以 enrich 后的 forbid_new_buys 为准；无该字段时回退旧 stress 逻辑
    if "forbid_new_buys" in micro:
        forbid_new_buys = score < 0.4 or bool(micro.get("forbid_new_buys"))
    else:
        forbid_new_buys = score < 0.4 or micro_regime == "liquidity_stress"
    block_offensive = bool(fw.get("block_offensive_buys"))
    prosperity_map = fw.get("prosperity_by_code") or {}
    inflection_map = fw.get("inflection_by_code") or {}
    prosperity_block = bool(fw.get("prosperity_block_adds", True))
    need_resonance = bool(fw.get("policy_requires_hard_resonance"))
    resonance_ok = bool(fw.get("hard_resonance_ok", True))
    info_map = info_completeness or {}
    earn_map = earnings_revisions or {}
    ocf_map = ocf_quality or {}

    holding_by_code = {
        "".join(ch for ch in str(h.get("code") or "") if ch.isdigit())[-6:].zfill(6): h
        for h in holdings
        if h.get("code")
    }
    paper_by_code = {
        "".join(ch for ch in str(h.get("code") or h.get("stock_code") or "") if ch.isdigit())[
            -6:
        ].zfill(6): h
        for h in (paper_holdings or [])
        if h.get("code") or h.get("stock_code")
    }
    operable_by_code = {**paper_by_code, **holding_by_code}
    is_empty_book = len(holding_by_code) == 0 and len(paper_by_code) == 0
    allow: set[str] | None = None
    if allowed_codes is not None:
        allow = {
            "".join(ch for ch in str(c) if ch.isdigit())[-6:].zfill(6)
            for c in allowed_codes
            if c
        }
        allow |= set(operable_by_code.keys())
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

        # Wave2：sector_link + 验证窗口（缺则补默认，不整轮失败）
        link, link_note = enrich_sector_link(
            rec,
            sector_analyses=sector_analyses,
            research_by_code=research_by_code,
        )
        rec["sector_link"] = link
        if link.get("sector") and not rec.get("sector_tag"):
            rec["sector_tag"] = link["sector"]
        if link_note:
            overrides.append(link_note)
        verify_fields, verify_note = enrich_verify_window(rec)
        rec.update(verify_fields)
        if verify_note:
            overrides.append(verify_note)

        # 空仓硬校验：无真实/纸面持仓时禁止 hold/sell/add
        if action in ("hold", "sell", "add") and code not in operable_by_code:
            new_act = "buy" if action == "add" else "watch"
            why = "空仓" if is_empty_book else "非持仓/非纸面仓"
            overrides.append(f"{code}: {why}禁止 {action}→{new_act}")
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

        ocf = ocf_map.get(code) or {}
        if (ocf.get("block_buy") or ocf.get("force_watch")) and action in ("buy", "add"):
            overrides.append(
                f"{code}: 现金流质量闸 → watch | "
                + "; ".join(ocf.get("evidence") or [])[:80]
            )
            action = "watch"
            rec["action"] = "watch"
            rec["position_pct"] = 0.0
            rec["ocf_quality"] = ocf

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
        if verify_conf_mult < 0.999 and action in ("buy", "add", "hold"):
            conf_f = max(0.05, conf_f * verify_conf_mult)
            overrides.append(f"{code}: verify先验置信×{verify_conf_mult}")
        rec["confidence"] = conf_f

        # verify 赛道低命中：禁新开
        sector_tag = str(
            rec.get("sector_tag")
            or (rec.get("sector_link") or {}).get("sector")
            or rec.get("sector")
            or ""
        ).strip()
        if (
            sector_tag
            and sector_tag in forbid_verify_sectors
            and action in ("buy", "add")
            and code not in holding_by_code
        ):
            overrides.append(f"{code}: 赛道[{sector_tag}]验证先验禁新开 → watch")
            action = "watch"
            rec["action"] = "watch"
            rec["position_pct"] = 0.0

        pos = rec.get("position_pct")
        try:
            pos_f = float(pos) if pos is not None else 0.0
        except (TypeError, ValueError):
            pos_f = 0.0

        if action in ("buy", "hold", "add"):
            if forbid_new_buys and action == "buy" and code not in operable_by_code:
                reason = (
                    f"微观结构{severity or micro_regime}禁新买"
                    if micro.get("forbid_new_buys")
                    else ("数据质量过低禁新买" if score < 0.4 else "风控禁新买")
                )
                overrides.append(f"{code}: {reason} → watch")
                action = "watch"
                rec["action"] = "watch"
                pos_f = 0.0
            if forbid_new_buys and action == "add" and bool(micro.get("forbid_new_buys")):
                overrides.append(f"{code}: 微观结构禁加仓 → hold")
                action = "hold"
                rec["action"] = "hold"

            # 景气 down：禁止 buy/add（拐点信号+证据可豁免）
            prosp = str(prosperity_map.get(code) or "").lower()
            if prosperity_block and prosp == "down" and action in ("buy", "add"):
                inf = inflection_map.get(code) or {}
                own_inf = rec.get("inflection_signal")
                own_ev = rec.get("inflection_evidence") or []
                if isinstance(own_ev, str):
                    own_ev = [own_ev] if own_ev.strip() else []
                exempt = bool(inf.get("signal") and inf.get("evidence")) or (
                    bool(own_inf) and bool(own_ev)
                )
                if exempt:
                    overrides.append(f"{code}: 景气down但拐点豁免{action}")
                    rec["inflection_exemption"] = True
                    rec["inflection_evidence"] = list(inf.get("evidence") or own_ev)[:4]
                else:
                    overrides.append(f"{code}: 板块景气down禁止{action} → watch/hold")
                    action = "hold" if code in holding_by_code else "watch"
                    rec["action"] = action
                    if action == "watch":
                        pos_f = 0.0

            # 矛盾激活：禁止进攻向 buy/add
            if block_offensive and action in ("buy", "add"):
                overrides.append(f"{code}: 硬事实/叙事矛盾激活 → 禁止进攻{action}")
                action = "hold" if code in holding_by_code else "watch"
                rec["action"] = action
                if action == "watch":
                    pos_f = 0.0

            # 政策须硬共振：无共振时不得新开/加仓
            if need_resonance and not resonance_ok and action in ("buy", "add"):
                overrides.append(f"{code}: 硬共振不足(政策单独不够) → 禁止{action}")
                action = "hold" if code in holding_by_code else "watch"
                rec["action"] = action
                if action == "watch":
                    pos_f = 0.0

            # 矛盾 haircut 置信度
            try:
                ch = float(fw.get("contradiction_haircut") or 1.0)
                if ch < 0.999 and action in ("buy", "add", "hold"):
                    conf_f = max(0.05, conf_f * ch)
                    rec["confidence"] = conf_f
            except (TypeError, ValueError):
                pass

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

        # 失效价带 / 观察目标：相对成本或现价做合理性夹逼（不作短线自动平仓依据）
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
            max_target = round(ref * (1 + take_profit_pct / 100 * 2), 4)  # 允许略超配置观察目标
            stop = rec.get("stop_loss")
            try:
                stop_f = float(stop) if stop is not None else min_stop
            except (TypeError, ValueError):
                stop_f = min_stop
            if stop_f < min_stop * 0.98:  # 失效价带过宽
                overrides.append(
                    f"{code}: 失效价带 {stop_f}→{min_stop} (max 偏离 {stop_loss_pct}%)"
                )
                stop_f = min_stop
            if stop_f >= ref:
                overrides.append(f"{code}: 失效价带 >= ref → {min_stop}")
                stop_f = min_stop
            rec["stop_loss"] = stop_f

            tgt = rec.get("target_price")
            try:
                tgt_f = float(tgt) if tgt is not None else round(ref * (1 + take_profit_pct / 100), 4)
            except (TypeError, ValueError):
                tgt_f = round(ref * (1 + take_profit_pct / 100), 4)
            if tgt_f <= ref:
                tgt_f = round(ref * (1 + take_profit_pct / 100), 4)
                overrides.append(f"{code}: 观察目标过低 → {tgt_f}")
            if tgt_f > max_target:
                overrides.append(f"{code}: 观察目标 {tgt_f}→{max_target}")
                tgt_f = max_target
            rec["target_price"] = tgt_f

        # 已持仓/纸面仓却标 buy → 改为 add
        if code in operable_by_code and action == "buy":
            rec["action"] = "add"
            action = "add"
            src = "纸面持仓" if code in paper_by_code and code not in holding_by_code else "已持仓"
            overrides.append(f"{code}: {src} buy→add")

        # 纸面仓不得晾成 watch：无卖出/失效时改为 hold，保证 A3 每轮有调仓指令
        if code in paper_by_code and action == "watch":
            rec["action"] = "hold"
            action = "hold"
            rec.setdefault("rationale", "")
            rec["rationale"] = (
                str(rec.get("rationale") or "") + " | 纸面持仓：watch→hold（非真实账户）"
            ).strip(" |")
            overrides.append(f"{code}: 纸面持仓 watch→hold")

        rec.setdefault("validation", {})
        rec["validation"] = {
            "regime_mult": regime_mult,
            "effective_max_total": effective_max_total,
            "data_quality_score": score,
        }
        # C7：门禁已把 action 翻到终局，重算 sector_link 叙述，避免与终局矛盾
        refresh_sector_link_rationale(rec, research_by_code=research_by_code)
        out.append(rec)

    # 确保每个真实/纸面持仓都有建议
    present = {r["code"] for r in out}
    for code, h in operable_by_code.items():
        if code not in present:
            ref = float(h.get("cost") or h.get("avg_cost") or 0) or quotes.get(code)
            is_paper = code in paper_by_code and code not in holding_by_code
            filled = {
                "code": code,
                "action": "hold",
                "confidence": 0.4,
                "position_pct": 0.0,
                "rationale": (
                    "系统补全：纸面持仓未出现在 LLM 建议中，默认 hold（非真实账户）"
                    if is_paper
                    else "系统补全：持仓未出现在 LLM 建议中，默认 hold"
                ),
                "stop_loss": round(float(ref) * (1 - stop_loss_pct / 100), 4) if ref else None,
                "target_price": round(float(ref) * (1 + take_profit_pct / 100), 4) if ref else None,
                "validation": {"auto_filled": True, "paper": is_paper},
            }
            link, _ = enrich_sector_link(
                filled, sector_analyses=sector_analyses, research_by_code=research_by_code
            )
            filled["sector_link"] = link
            if link.get("sector"):
                filled["sector_tag"] = link["sector"]
            vf, _ = enrich_verify_window(filled)
            filled.update(vf)
            out.append(filled)
            overrides.append(
                f"{code}: 补全缺失{'纸面' if is_paper else ''}持仓建议 → hold"
            )

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

    # 主题集中度：同一主题 buy/add 只数不超过 max_theme_names（对齐筛股 max_deep_per_theme）
    by_theme: dict[str, list[dict[str, Any]]] = {}
    for r in out:
        if str(r.get("action")).lower() not in ("buy", "add"):
            continue
        if float(r.get("position_pct") or 0) <= 0 and str(r.get("action")).lower() == "add":
            continue
        if str(r.get("action")).lower() == "buy" and float(r.get("position_pct") or 0) <= 0:
            continue
        theme = str(
            r.get("sector_tag")
            or (r.get("sector_link") or {}).get("sector")
            or r.get("sector")
            or "unknown"
        )
        if theme == "unknown":
            continue
        by_theme.setdefault(theme, []).append(r)
    for theme, rows in by_theme.items():
        if len(rows) <= max_theme_names:
            continue
        # 保留置信度更高的前 N，其余压 watch
        ranked = sorted(rows, key=lambda x: float(x.get("confidence") or 0), reverse=True)
        for r in ranked[max_theme_names:]:
            if str(r.get("action")).lower() == "buy" and r["code"] not in holding_by_code:
                overrides.append(
                    f"{r['code']}: 主题[{theme}]超限(≤{max_theme_names}) → watch"
                )
                r["action"] = "watch"
                r["position_pct"] = 0.0

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
