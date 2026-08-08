"""中长线框架闸：矛盾、政策共振、景气、phase 升档（规则层，供校验与 LLM）。"""

from __future__ import annotations

from typing import Any

from money_more.analysis.sector_map import infer_sector
from money_more.config import FrameworkGateConfig
from money_more.data.fetcher import _safe_float, normalize_code


def build_code_prosperity_map(
    sector_analyses: list[dict[str, Any]] | None,
    stock_analyses: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """code -> prosperity (up|flat|down)。优先个股分析里的板块景气，否则按板块名映射。"""
    sector_pros: dict[str, str] = {}
    for sec in sector_analyses or []:
        a = sec.get("analysis") or {}
        name = str(a.get("sector") or sec.get("sector") or "").strip()
        pros = str(a.get("prosperity") or "").lower().strip()
        if name and pros in ("up", "flat", "down"):
            sector_pros[name] = pros
            # 短名别名
            for key in (name, name.replace("板块", "")):
                if key:
                    sector_pros[key] = pros

    out: dict[str, str] = {}
    for item in stock_analyses or []:
        code = normalize_code(str(item.get("code") or ""))
        if not code:
            continue
        a = item.get("analysis") or {}
        # 个股分析若自带景气
        own = str(a.get("prosperity") or a.get("sector_prosperity") or "").lower()
        if own in ("up", "flat", "down"):
            out[code] = own
            continue
        sector = str(a.get("sector") or item.get("sector") or infer_sector(code) or "")
        for key, pros in sector_pros.items():
            if key and key in sector:
                out[code] = pros
                break
        if code not in out and sector in sector_pros:
            out[code] = sector_pros[sector]
    return out


def _truthy_inflection(a: dict[str, Any]) -> tuple[bool, list[str]]:
    signal = a.get("inflection_signal")
    if isinstance(signal, str):
        signal = signal.strip().lower() in ("true", "1", "yes", "y")
    else:
        signal = bool(signal)
    ev = a.get("inflection_evidence") or a.get("inflection_evidence_list") or []
    if isinstance(ev, str):
        evidence = [ev] if ev.strip() else []
    else:
        evidence = [str(x).strip() for x in (ev or []) if str(x).strip()]
    return signal and bool(evidence), evidence


def build_code_inflection_map(
    sector_analyses: list[dict[str, Any]] | None,
    stock_analyses: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """code -> {signal, evidence}；仅当信号为真且有证据。"""
    sector_inf: dict[str, dict[str, Any]] = {}
    for sec in sector_analyses or []:
        a = sec.get("analysis") or {}
        name = str(a.get("sector") or sec.get("sector") or "").strip()
        ok, evidence = _truthy_inflection(a)
        if name and ok:
            payload = {"signal": True, "evidence": evidence[:4]}
            for key in (name, name.replace("板块", "")):
                if key:
                    sector_inf[key] = payload

    out: dict[str, dict[str, Any]] = {}
    for item in stock_analyses or []:
        code = normalize_code(str(item.get("code") or ""))
        if not code:
            continue
        a = item.get("analysis") or {}
        ok, evidence = _truthy_inflection(a)
        if ok:
            out[code] = {"signal": True, "evidence": evidence[:4]}
            continue
        sector = str(a.get("sector") or item.get("sector") or infer_sector(code) or "")
        for key, payload in sector_inf.items():
            if key and key in sector:
                out[code] = payload
                break
        if code not in out and sector in sector_inf:
            out[code] = sector_inf[sector]
    return out


def _latest_macro_value(macro_hard: dict[str, Any], key: str, field_hints: tuple[str, ...]) -> float | None:
    rows = macro_hard.get(key) or []
    if not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    for h in field_hints:
        for k, v in row.items():
            if h in str(k):
                val = _safe_float(v)
                if val is not None:
                    return val
    # 任意数值列
    for v in row.values():
        val = _safe_float(v)
        if val is not None and 0 < val < 200:
            return val
    return None


def detect_hard_contradictions(macro_intel: dict[str, Any] | None) -> list[str]:
    """硬事实层面的矛盾线索（非 LLM 散文）。"""
    macro = macro_intel or {}
    hard = macro.get("macro_hard") or {}
    flags: list[str] = []
    pmi = _latest_macro_value(hard, "pmi", ("制造业", "PMI", "指数"))
    if pmi is not None and pmi < 50:
        flags.append(f"PMI收缩({pmi})")
    shr = hard.get("social_financing") or hard.get("shrzgm") or []
    # 社融同比/环比若在记录里，仅作旁证；缺字段则跳过
    margin = macro.get("margin_trend") or {}
    chg5 = _safe_float(margin.get("financing_balance_change_5d_pct"))
    if chg5 is not None and chg5 < 0:
        flags.append(f"融资余额近窗收缩({chg5}%)")
    _ = shr  # 社融作旁路输入，不单列为矛盾
    return flags


def has_hard_resonance(macro_intel: dict[str, Any] | None, microstructure: dict[str, Any] | None) -> bool:
    """买入所需硬共振：流动性/资金/微观传导至少一项尚可。"""
    macro = macro_intel or {}
    gl = macro.get("global_liquidity") or {}
    stance = str(gl.get("stance") or "unknown")
    if stance in ("easing", "mixed", "neutral"):
        return True
    if sector_money_flow_ok(macro.get("sector_money_flow")):
        return True
    micro = microstructure or {}
    if micro.get("fundamental_channel_ok") and str(micro.get("severity") or "") in (
        "",
        "none",
        "mild",
    ):
        if stance != "tightening":
            return True
    eb = macro.get("equity_bond") or gl.get("equity_bond") or {}
    if eb.get("ok") and str(eb.get("regime") or "") in ("attractive", "neutral"):
        return True
    return False


def sector_money_flow_ok(flow: Any) -> bool:
    if not flow:
        return False
    if isinstance(flow, dict):
        return bool(flow.get("top_inflow") or flow.get("industries") or flow.get("rows"))
    return bool(flow)


def build_framework_gate_state(
    *,
    config: FrameworkGateConfig,
    market_analysis: dict[str, Any] | None,
    macro_intel: dict[str, Any] | None,
    microstructure: dict[str, Any] | None,
    prior_context: dict[str, Any] | None,
    sector_analyses: list[dict[str, Any]] | None = None,
    stock_analyses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    market = market_analysis or {}
    hard_flags = detect_hard_contradictions(macro_intel)
    llm_contras = market.get("contradictions") or market.get("key_contradictions") or []
    contra_texts = [str(x) for x in llm_contras if x][:6]
    contradiction_active = bool(hard_flags or contra_texts)

    resonance = has_hard_resonance(macro_intel, microstructure)
    prior_hist = (prior_context or {}).get("market_history") or []
    prior = prior_hist[0] if prior_hist else {}
    prior_phase = str(prior.get("phase") or "").lower()
    prior_style = str(prior.get("style") or "")
    prior_risk = str(prior.get("risk_level") or "").lower()
    prior_micro = str(prior.get("micro_regime") or prior.get("microstructure_regime") or "")

    cur_phase = str(market.get("phase") or "").lower()
    cur_style = str(market.get("style") or "")
    cur_risk = str(market.get("risk_level") or "").lower()

    phase_upgrade = _is_phase_upgrade(prior_phase, cur_phase) or _is_risk_downgrade(
        prior_risk, cur_risk
    )
    style_to_growth = _style_shifted_to_growth(prior_style, cur_style)
    micro_regime = str((microstructure or {}).get("regime") or "normal")
    micro_blocks = micro_regime in ("liquidity_stress", "crowded_sync") or bool(
        (microstructure or {}).get("forbid_new_buys")
    )

    block_phase_upgrade = bool(
        config.phase_upgrade_needs_confirm
        and phase_upgrade
        and (micro_blocks or contradiction_active or prior_micro in ("liquidity_stress", "crowded_sync"))
    )

    return {
        "contradiction_active": contradiction_active,
        "hard_contradiction_flags": hard_flags,
        "llm_contradictions": contra_texts,
        "hard_resonance_ok": resonance,
        "policy_requires_hard_resonance": config.policy_requires_hard_resonance,
        "contradiction_haircut": float(config.contradiction_haircut)
        if contradiction_active
        else 1.0,
        "block_offensive_buys": bool(
            config.contradiction_block_offensive and contradiction_active
        ),
        "block_phase_upgrade": block_phase_upgrade,
        "style_shift_to_growth": style_to_growth,
        "prosperity_by_code": build_code_prosperity_map(sector_analyses, stock_analyses),
        "inflection_by_code": build_code_inflection_map(sector_analyses, stock_analyses),
        "prosperity_block_adds": config.prosperity_block_adds,
        "plain_note": _plain_note(
            contradiction_active, resonance, block_phase_upgrade, hard_flags
        ),
    }


def clamp_market_optimism(
    market_analysis: dict[str, Any],
    gate_state: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """升乐观过快时压回：降 risk 上调、风格冲成长、phase 升档。"""
    overrides: list[str] = []
    out = dict(market_analysis or {})
    if not gate_state.get("block_phase_upgrade"):
        return out, overrides

    # 风险不得因本轮乐观而从 high 直接降到 medium/low
    risk = str(out.get("risk_level") or "").lower()
    if risk in ("medium", "low", "中", "低", "中性"):
        out["risk_level"] = "high"
        overrides.append("framework: phase/style 升档受阻 → risk 维持 high")

    style = str(out.get("style") or "")
    if gate_state.get("style_shift_to_growth") and any(
        k in style for k in ("成长", "growth", "进攻", "科技")
    ):
        out["style"] = "偏防御/均衡（升档待周频确认）"
        overrides.append("framework: 风格切换至成长被拦截，待周频确认")

    vs = str(out.get("vs_prior") or out.get("relative_to_prior") or "")
    if vs.lower() in ("shift", "转向", "improve", "改善"):
        out["vs_prior"] = "hold — 微观/矛盾未解除，不认短期转向"
        overrides.append("framework: vs_prior 从转向压回 hold")

    conf = _safe_float(out.get("confidence"))
    if conf is not None:
        out["confidence"] = round(max(0.15, conf * 0.85), 3)
    return out, overrides


def _is_phase_upgrade(prior: str, cur: str) -> bool:
    order = {
        "panic": 0,
        "bear": 1,
        "快速下跌": 1,
        "downtrend": 1,
        "range": 2,
        "震荡": 2,
        "筑底": 2,
        "bull": 3,
        "偏强": 3,
        "uptrend": 3,
    }
    def rank(p: str) -> int:
        p = (p or "").lower()
        for k, v in order.items():
            if k in p:
                return v
        return 2

    return rank(cur) > rank(prior) and prior != ""


def _is_risk_downgrade(prior: str, cur: str) -> bool:
    order = {"high": 3, "高": 3, "elevated": 2, "偏高": 2, "medium": 1, "中": 1, "low": 0, "低": 0}

    def rank(r: str) -> int:
        r = (r or "").lower()
        for k, v in order.items():
            if k in r:
                return v
        return 1

    return prior != "" and rank(cur) < rank(prior)


def _style_shifted_to_growth(prior: str, cur: str) -> bool:
    def growth(s: str) -> bool:
        s = s or ""
        return any(k in s for k in ("成长", "growth", "进攻", "科技", "硬科技"))

    def defense(s: str) -> bool:
        s = s or ""
        return any(k in s for k in ("防御", "价值", "高股息", "defense", "value"))

    return defense(prior) and growth(cur)


def _plain_note(
    contradiction_active: bool,
    resonance: bool,
    block_phase_upgrade: bool,
    hard_flags: list[str],
) -> str:
    bits = []
    if contradiction_active:
        bits.append("矛盾激活→压进攻")
    if not resonance:
        bits.append("硬共振不足→政策单独不够买")
    if block_phase_upgrade:
        bits.append("phase/风格升档待确认")
    if hard_flags:
        bits.append("硬标志:" + ",".join(hard_flags[:3]))
    return "；".join(bits) if bits else "框架闸正常"
