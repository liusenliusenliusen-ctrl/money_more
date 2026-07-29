from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from money_more.analysis.sector_map import infer_sector
from money_more.utils.json_util import dumps_json

_ACTION_LABEL = {
    "buy": "买入",
    "add": "加仓",
    "sell": "卖出",
    "hold": "持有",
    "watch": "观察",
}


def _fmt_sentiment(sent: dict[str, Any] | None) -> str:
    if not sent:
        return ""
    parts = []
    for key in ("level", "overall", "news_tone", "crowding_risk", "research_consensus"):
        if sent.get(key):
            parts.append(f"{key}={sent[key]}")
    return " | ".join(parts)


def _one_line(text: Any, limit: int | None = 72) -> str:
    """压空白；limit=None 时不截断。"""
    s = " ".join(str(text or "").split())
    if limit is None or len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _stock_name_map(result: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for st in result.get("stocks") or []:
        a = st.get("analysis") or {}
        code = str(a.get("code") or st.get("code") or "")
        name = str(a.get("name") or "")
        if code:
            out[code] = name
    return out


def _recs_by_sector(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_sec: dict[str, list[dict[str, Any]]] = {}
    for rec in result.get("recommendations") or []:
        code = str(rec.get("code") or "")
        tag = str(rec.get("sector_tag") or "") or (infer_sector(code) or "")
        if not tag:
            continue
        by_sec.setdefault(tag, []).append(rec)
    return by_sec


def _sector_stance(analysis: dict[str, Any], related: list[dict[str, Any]]) -> str:
    """由板块结论 + 对应个股动作派生一句态度（非再调 LLM）。"""
    priority = str(analysis.get("priority") or "medium")
    valuation = str(analysis.get("valuation") or "")
    prosperity = str(analysis.get("prosperity") or "")
    crowding = str((analysis.get("sentiment") or {}).get("crowding_risk") or "")
    actions = {str(r.get("action") or "watch") for r in related}

    if "sell" in actions:
        return "组合侧规避/减仓"
    if crowding == "high" or valuation == "expensive":
        return "逻辑可跟，回避追高（等回调）"
    if actions & {"buy", "add"}:
        return "可配置（见下方动作）"
    if "hold" in actions:
        return "已有敞口则持有观察"
    if "watch" in actions:
        return "赛道可看，个股等确认再动"
    if priority == "high" and valuation == "cheap" and prosperity == "up":
        return "左侧可关注，等资金/催化剂"
    if priority == "high":
        return "高优先级跟踪，不宜盲目加仓"
    if prosperity == "down":
        return "景气偏弱，反弹宜谨慎"
    return "中性跟踪"


def _render_contested_block(
    result: dict[str, Any],
    *,
    heading: str,
    limit: int | None = None,
    include_policy: bool = True,
) -> list[str]:
    """争议叙事 / 尾部情景侧栏（信号与含义全文，不截断）。"""
    market = (result.get("market") or {}).get("analysis") or {}
    summary = result.get("decision_summary") or {}
    items = list(market.get("contested_narratives") or summary.get("contested_narratives") or [])
    pol = market.get("policy_market_scenario") or summary.get("policy_market_scenario") or {}
    if not items and not (include_policy and pol):
        return []
    lines = [heading, ""]
    shown = items if limit is None else items[:limit]
    for item in shown:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or "-"
        src = item.get("source_type") or "-"
        prob = item.get("probability") or "-"
        lines.append(f"- **{title}** · 来源 `{src}` · 概率粗分 `{prob}`")
        if item.get("confirm_signals"):
            lines.append(
                f"  - 确认: {'；'.join(_one_line(x, None) for x in item['confirm_signals'])}"
            )
        if item.get("falsify_signals"):
            lines.append(
                f"  - 证伪: {'；'.join(_one_line(x, None) for x in item['falsify_signals'])}"
            )
        if item.get("portfolio_if_true"):
            lines.append(f"  - 若成立: {_one_line(item.get('portfolio_if_true'), None)}")
    if include_policy:
        if pol and pol.get("status") and pol.get("status") != "inactive":
            lines.append(
                f"- **政策市假说** `{pol.get('status')}`: "
                f"{_one_line(pol.get('title') or pol.get('thesis'), None)}"
            )
            if pol.get("implication"):
                lines.append(f"  - 若成立: {_one_line(pol.get('implication'), None)}")
        elif pol and pol.get("title"):
            lines.append(
                f"- **政策市假说** `inactive`（模板待命）: {_one_line(pol.get('title'), None)}"
            )
    lines.append("")
    return lines


def _fmt_stage_action(rec: dict[str, Any] | None) -> str:
    if not rec:
        return "-"
    action = str(rec.get("action") or "watch")
    label = _ACTION_LABEL.get(action, action)
    if rec.get("selection") == "not_selected":
        return f"{label}·未入选"
    try:
        pct = rec.get("position_pct")
        pct_s = f"{float(pct):.0f}%" if pct is not None and float(pct) != 0 else ""
    except (TypeError, ValueError):
        pct_s = ""
    conf = rec.get("confidence")
    conf_s = f" conf={conf}" if conf is not None else ""
    extra = ""
    if rec.get("referee"):
        extra = f" 裁判={rec.get('referee')}"
    elif rec.get("debate_status") == "undebated":
        extra = " 未辩论"
    elif rec.get("debate_status") == "n/a" and action == "watch":
        extra = " 跳过辩论"
    return f"{label}{(' ' + pct_s) if pct_s else ''}{conf_s}{extra}"


def _count_buy_add(recs: list[dict[str, Any]] | None) -> int:
    n = 0
    for r in recs or []:
        if r.get("selection") == "not_selected":
            continue
        if str(r.get("action") or "").lower() in ("buy", "add"):
            try:
                if r.get("position_pct") is not None and float(r.get("position_pct")) <= 0:
                    continue
            except (TypeError, ValueError):
                pass
            n += 1
    return n


def _multi_agent_synthesis_note(result: dict[str, Any]) -> str:
    """结论卡用：说明②来自双分析师→综合，并给本轮计数对照。"""
    ma = result.get("multi_agent") or {}
    stages = result.get("decision_stages") or {}
    audit = stages.get("synthesis_audit") if isinstance(stages.get("synthesis_audit"), dict) else None
    if not ma.get("enabled"):
        return (
            "本轮未开多 Agent：②由单一决策模型直接给出组合动作/仓位"
            "（仍≠①研究评级）。"
        )
    meta = ma.get("meta") if isinstance(ma.get("meta"), dict) else {}
    primary = str(meta.get("primary") or "?")
    secondary = str(meta.get("secondary") or "?")
    synth = str(meta.get("synthesizer") or "synthesizer")
    draft_n = _count_buy_add(stages.get("portfolio_draft"))
    if audit and audit.get("agent_buy_counts"):
        bits = [f"{k} 建议买入/加仓 {v} 只" for k, v in audit["agent_buy_counts"].items()]
        dropped = audit.get("dropped_buys") or []
        drop_s = (
            f"；综合否决买入 {len(dropped)} 只"
            + (f"（{'、'.join(dropped[:6])}{'…' if len(dropped) > 6 else ''}）" if dropped else "")
        )
        contrast = "；".join(bits) + f" → **综合后写入②：买入/加仓 {draft_n} 只**" + drop_s
    else:
        drafts = result.get("multi_agent_drafts") or {}
        bits = []
        for name in (primary, secondary):
            if name in drafts and isinstance(drafts[name], dict):
                n = _count_buy_add(drafts[name].get("recommendations"))
                bits.append(f"{name} 建议买入/加仓 {n} 只")
            elif name not in ("?", ""):
                bits.append(f"{name}（独立草案）")
        contrast = ("；".join(bits) + f" → **综合后写入②：买入/加仓 {draft_n} 只**") if bits else (
            f"综合后写入②：买入/加仓 {draft_n} 只"
        )
    return (
        f"启用多 Agent：`{primary}` + `{secondary}` 各出独立组合草案，"
        f"再由 **`{synth}` 综合委员** 合并取舍后写入下表②。"
        f" {contrast}。"
        "① 的 research buy 再多，也不自动进入②。"
    )


def render_decision_stages_section(result: dict[str, Any]) -> list[str]:
    """展示 研究→草案→辩论→风控 分阶段结论。"""
    stages = result.get("decision_stages") or {}
    if not stages:
        return []
    lines: list[str] = []
    lines.append("## 决策流程（分阶段结论）")
    lines.append("")
    lines.append(
        "_下表是个股决策链一览（结论卡速读）；**按票完整推理见详细论证 B2**；"
        "结论卡 **A3** 只列④终局指令。_"
    )
    lines.append("")
    lines.append("**步骤说明（先读再看表）**")
    lines.append("")
    lines.append(
        "- **① 个股研究**：对深度池**每一只**做单票研究，产出 `research_rating`"
        "（如 buy/hold）。回答「这只票基本面/叙事怎么看」；**不是**下单指令。"
    )
    lines.append(
        "- **② 组合草案**：在①之上做**组合层取舍**（买哪些、仓位多少、其余是否搁置）。"
        f"{_multi_agent_synthesis_note(result)}"
    )
    lines.append(
        "- **③ 多空辩论**：只对②里 **已入选且 buy/add** 的票开多空对抗；"
        "「观察·未入选」跳过辩论（不是漏跑）。"
    )
    lines.append(
        "- **④ 风控终局**：叠硬门禁/微观结构/总仓/流动性等规则后的**可执行动作**；"
        "只有已入选且 buy/add（仓位>0）才进模拟盘与结论卡 A3。"
        "「观察·未入选」=组合层有意搁置。"
    )
    lines.append("")
    flow = stages.get("flow") or []
    if flow:
        lines.append("**本轮链路**: " + " → ".join(str(x) for x in flow))
        lines.append("")

    audit = stages.get("synthesis_audit") if isinstance(stages.get("synthesis_audit"), dict) else None
    if audit:
        dropped = audit.get("dropped_buys") or []
        kept = audit.get("synthesized_buys") or []
        lines.append(
            "**综合取舍审计**: "
            f"写入②买入 {len(kept)} 只"
            + (f"（{'、'.join(kept[:8])}）" if kept else "")
            + f"；否决买入 {len(dropped)} 只"
            + (f"（{'、'.join(dropped[:8])}{'…' if len(dropped) > 8 else ''}）" if dropped else "")
            + "。"
        )
        lines.append("")

    summary = result.get("decision_summary") or {}
    final_sum = stages.get("final_portfolio_summary") or summary.get("portfolio_summary") or ""
    draft_sum = stages.get("draft_portfolio_summary") or summary.get("portfolio_summary_draft") or ""
    # 先草案、后终局，便于对照「被覆盖前」与「可执行后」
    if draft_sum and draft_sum.strip() and draft_sum.strip() != str(final_sum).strip():
        lines.append(
            f"**②草案摘要（综合后 · 已被终局覆盖，仅供对照）**: {_one_line(draft_sum, None)}"
        )
        lines.append("")
    if final_sum:
        lines.append(f"**④终局组合摘要**: {final_sum}")
        lines.append("")

    research = {str(r.get("code")): r for r in (stages.get("research") or [])}
    draft = {str(r.get("code")): r for r in (stages.get("portfolio_draft") or [])}
    debated = {str(r.get("code")): r for r in (stages.get("after_debate") or [])}
    final = {str(r.get("code")): r for r in (stages.get("after_risk") or [])}
    codes: list[str] = []
    for src in (final, debated, draft, research):
        for c in src:
            if c and c not in codes:
                codes.append(c)

    if codes:
        lines.append("| 代码 | ①研究评级 | ②组合草案 | ③辩论后 | ④风控终局 |")
        lines.append("|------|-----------|-----------|---------|-----------|")
        names = _stock_name_map(result)
        for code in codes:
            r0 = research.get(code) or {}
            rating = str(r0.get("research_rating") or "-")
            conf0 = r0.get("confidence")
            rating_s = rating + (f" ({conf0})" if conf0 is not None else "")
            name = names.get(code) or r0.get("name") or ""
            code_s = f"{code}" + (f" {name}" if name else "")
            lines.append(
                f"| {code_s} | {rating_s} | {_fmt_stage_action(draft.get(code))} | "
                f"{_fmt_stage_action(debated.get(code))} | {_fmt_stage_action(final.get(code))} |"
            )
        lines.append("")

    if stages.get("plain_note"):
        lines.append(f"_{stages['plain_note']}_")
        lines.append("")
    return lines


def _index_stage_by_code(rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows or []:
        code = str(r.get("code") or "")
        if code:
            out[code] = r
    return out


def _fmt_chain_step(label: str, action_or_rating: str, *, pct: Any = None, conf: Any = None, extra: str = "") -> str:
    if action_or_rating in _ACTION_LABEL:
        act = _ACTION_LABEL[action_or_rating]
    else:
        act = action_or_rating or "-"
    bits = [f"{label}{act}"]
    try:
        if pct is not None and float(pct) != 0:
            bits.append(f"{float(pct):.0f}%")
    except (TypeError, ValueError):
        pass
    if conf is not None:
        bits.append(f"conf={conf}")
    if extra:
        bits.append(extra)
    return " ".join(bits)


def build_stock_chain_bridge(
    code: str,
    result: dict[str, Any],
    *,
    final_rec: dict[str, Any] | None = None,
) -> str:
    """一行：①研究 → ②草案 → ③辩论 → ④终局。"""
    stages = result.get("decision_stages") or {}
    research = _index_stage_by_code(stages.get("research")).get(code) or {}
    draft = _index_stage_by_code(stages.get("portfolio_draft")).get(code) or {}
    debated = _index_stage_by_code(stages.get("after_debate")).get(code) or {}
    risked = _index_stage_by_code(stages.get("after_risk")).get(code) or {}
    final = final_rec or risked or {}

    # fallback research from stocks
    if not research:
        for st in result.get("stocks") or []:
            a = st.get("analysis") or {}
            if str(a.get("code") or st.get("code") or "") == code:
                research = {
                    "research_rating": a.get("research_rating"),
                    "confidence": a.get("confidence"),
                }
                break

    s1 = _fmt_chain_step(
        "①",
        str(research.get("research_rating") or "-"),
        conf=research.get("confidence"),
    )
    s2 = _fmt_chain_step(
        "②",
        str(draft.get("action") or "-"),
        pct=draft.get("position_pct"),
        conf=draft.get("confidence"),
    )
    ref = debated.get("referee")
    s3 = _fmt_chain_step(
        "③",
        str(debated.get("action") or "-"),
        pct=debated.get("position_pct"),
        conf=debated.get("confidence"),
        extra=f"裁判={ref}" if ref else ("未辩论" if debated.get("debate_status") == "undebated" else ""),
    )
    s4 = _fmt_chain_step(
        "④",
        str(final.get("action") or risked.get("action") or "-"),
        pct=final.get("position_pct") if final.get("position_pct") is not None else risked.get("position_pct"),
        conf=final.get("confidence") if final.get("confidence") is not None else risked.get("confidence"),
    )
    return f"{s1} → {s2} → {s3} → {s4}"


def _overrides_for_code(overrides: list[str], code: str) -> list[str]:
    prefix = f"{code}:"
    return [o for o in overrides if str(o).startswith(prefix) or f" {code}:" in str(o)]


def render_stock_decision_chains(result: dict[str, Any]) -> list[str]:
    """详细论证 B2：每只票完整决策链（研究→草案→辩论→风控）。"""
    lines: list[str] = []
    lines.append("#### B2. 个股决策链（①研究→②草案→③辩论→④风控）")
    lines.append("")
    lines.append(
        "_核对结论卡 **B2**（含步骤说明）。每只票写完整推理链；"
        "**①研究评级 ≠ 可开仓指令**；**②含综合取舍**。"
        "可执行列表见结论卡 **A3**；止损/目标等纪律字段写在各票 **④**。_"
    )
    lines.append("")

    stages = result.get("decision_stages") or {}
    draft_by = _index_stage_by_code(stages.get("portfolio_draft"))
    debate_stage_by = _index_stage_by_code(stages.get("after_debate"))
    risk_by = _index_stage_by_code(stages.get("after_risk"))
    rec_by = {str(r.get("code") or ""): r for r in (result.get("recommendations") or [])}
    debates = result.get("debates") or {}
    overrides = list(result.get("validation_overrides") or (result.get("decision_summary") or {}).get("validation_overrides") or [])

    # 顺序：建议列表优先，再补深度池其余票
    codes: list[str] = []
    for rec in result.get("recommendations") or []:
        c = str(rec.get("code") or "")
        if c and c not in codes:
            codes.append(c)
    for st in result.get("stocks") or []:
        a = st.get("analysis") or {}
        c = str(a.get("code") or st.get("code") or "")
        if c and c not in codes:
            codes.append(c)

    stock_by: dict[str, dict[str, Any]] = {}
    for st in result.get("stocks") or []:
        a = st.get("analysis") or {}
        c = str(a.get("code") or st.get("code") or "")
        if c:
            stock_by[c] = st

    names = _stock_name_map(result)
    if not codes:
        lines.append("_（本轮无深度个股）_")
        lines.append("")
        return lines

    for code in codes:
        st = stock_by.get(code) or {}
        a = st.get("analysis") or {}
        name = names.get(code) or a.get("name") or ""
        rec = rec_by.get(code) or {}
        bridge = build_stock_chain_bridge(code, result, final_rec=rec or risk_by.get(code))

        lines.append(f"##### {code}{(' ' + name) if name else ''}")
        lines.append("")
        lines.append(f"**决策链**: {bridge}")
        lines.append("")

        # ① 研究
        lines.append("###### ① 研究（基本面 / 赔率 / 叙事）")
        lines.append("")
        lines.append(
            f"- **研究评级**: `{a.get('research_rating', '-')}` · "
            f"质量:{a.get('quality', '-')} · 估值:{a.get('valuation', '-')}"
            + (f" · 置信度 {a.get('confidence')}" if a.get("confidence") is not None else "")
        )
        if a.get("investment_thesis"):
            lines.append(f"- **投资逻辑**: {a['investment_thesis']}")
        sent = _fmt_sentiment(a.get("sentiment"))
        quant = a.get("sentiment") or {}
        if quant.get("quant_score_100") is not None:
            lines.append(
                f"- **量化舆情**: {quant.get('quant_score_100')}/100 ({quant.get('quant_label', '-')})"
            )
        if sent:
            lines.append(f"- **舆情**: {sent}")
        if a.get("expectation_gap"):
            lines.append(f"- **预期差**: {a['expectation_gap']}")
        info = st.get("info_completeness") or a.get("info_completeness") or {}
        if info.get("status"):
            lines.append(
                f"- **信息完备性**: `{info.get('status')}`"
                f"（{info.get('severity', '-')}）— {_one_line(info.get('note'), 80)}"
            )
            if info.get("unexplained"):
                lines.append(
                    "- 缺口线索: "
                    + "；".join(_one_line(x, 40) for x in info["unexplained"][:2])
                )
            if a.get("info_gap_note"):
                lines.append(f"- {_one_line(a.get('info_gap_note'), 100)}")
        earn = st.get("earnings_revision") or a.get("earnings_revision") or {}
        if earn.get("signal"):
            lines.append(
                f"- **盈利预期修正**: `{earn.get('signal')}` / bias={earn.get('revision_bias')} "
                f"— {_one_line(earn.get('note'), 80)}"
            )
            if earn.get("evidence"):
                lines.append(
                    "- 修正证据: "
                    + "；".join(_one_line(x, 40) for x in earn["evidence"][:2])
                )
            if a.get("earnings_revision_note"):
                lines.append(f"- {_one_line(a.get('earnings_revision_note'), 100)}")
            if a.get("earnings_revision_override"):
                lines.append(f"- ⚠ {a['earnings_revision_override']}")
        sc = st.get("factor_scorecard") or a.get("factor_scorecard") or rec.get("factor_scorecard") or {}
        if sc.get("total_score") is not None:
            scores = sc.get("scores") or {}
            parts = " · ".join(f"{k}={v}" for k, v in scores.items())
            lines.append(
                f"- **因子分**: **{sc.get('total_score')}** ({sc.get('signal')}) | {parts}"
            )
        if a.get("summary"):
            lines.append(f"- **研究小结**: {a.get('summary')}")
        if not a and not sc:
            lines.append("- _（本轮无独立研究产出，仅有组合/风控层记录）_")
        lines.append("")

        # ② 草案
        lines.append("###### ② 组合草案")
        lines.append("")
        d = draft_by.get(code)
        if d and d.get("selection") == "not_selected":
            lines.append("- **草案动作**: 观察·未入选（综合未写入组合；≠漏跑）")
            if d.get("rationale"):
                lines.append(f"- **说明**: {d['rationale']}")
        elif d:
            lines.append(
                f"- **草案动作**: {_ACTION_LABEL.get(str(d.get('action')), d.get('action'))}"
                + (f" · 仓位 {d.get('position_pct')}%" if d.get("position_pct") is not None else "")
                + (f" · 置信度 {d.get('confidence')}" if d.get("confidence") is not None else "")
            )
            if d.get("rationale"):
                lines.append(f"- **草案理由**: {d['rationale']}")
        else:
            lines.append("- _本轮组合草案未覆盖该代码（或未写入 decision_stages）_")
        # 证据链更贴近草案/研究交接
        if rec.get("evidence_chain"):
            lines.append("- **证据链**:")
            for ev in rec["evidence_chain"][:5]:
                lines.append(f"  - {ev}")
        lines.append("")

        # ③ 辩论
        lines.append("###### ③ 多空辩论")
        lines.append("")
        debate = rec.get("debate") or debates.get(code) or {}
        ds = debate_stage_by.get(code) or {}
        status = rec.get("debate_status") or ds.get("debate_status")
        if ds.get("selection") == "not_selected" or d and d.get("selection") == "not_selected":
            lines.append("- _未入选组合，跳过辩论_")
        elif debate and not debate.get("error"):
            lines.append(
                f"- **裁判**: `{debate.get('referee', '-')}` · "
                f"haircut={debate.get('confidence_haircut', '-')} · "
                f"hint={debate.get('decision_hint', '-')}"
            )
            if debate.get("bull_case"):
                lines.append(f"- **多头**: {_one_line(debate.get('bull_case'), 160)}")
            if debate.get("bear_case"):
                lines.append(f"- **空头**: {_one_line(debate.get('bear_case'), 160)}")
            if ds.get("action"):
                lines.append(
                    f"- **辩论后动作**: {_ACTION_LABEL.get(str(ds.get('action')), ds.get('action'))}"
                    + (f" · 仓位 {ds.get('position_pct')}%" if ds.get("position_pct") is not None else "")
                    + (f" · conf={ds.get('confidence')}" if ds.get("confidence") is not None else "")
                )
        elif status == "undebated":
            lines.append(
                "- **未辩论**（buy/add 本应全量辩论；缺失时宜更保守，且通常被风控降为观察）"
            )
        elif status == "n/a" or (
            str(ds.get("action") or rec.get("action") or "watch") == "watch" and not debate
        ):
            lines.append("- _非 buy/add 草案，未进入多空对抗（或无需辩论）_")
        else:
            lines.append("- _无辩论记录_")
        lines.append("")

        # ④ 风控
        lines.append("###### ④ 风控终局")
        lines.append("")
        risk_row = risk_by.get(code) or {}
        final = rec or risk_row or {}
        if (not rec) and risk_row.get("selection") == "not_selected":
            lines.append("- **终局动作**: **观察·未入选**（未进入可执行建议列表 / A3）")
            if risk_row.get("rationale"):
                lines.append(f"- **说明**: {risk_row['rationale']}")
            lines.append("")
            continue
        faction = str(final.get("action") or "watch")
        lines.append(
            f"- **终局动作**: **{_ACTION_LABEL.get(faction, faction)}**"
            + (f" · 仓位 {final.get('position_pct')}%" if final.get("position_pct") is not None else "")
            + (f" · 置信度 {final.get('confidence')}" if final.get("confidence") is not None else "")
        )
        code_ov = _overrides_for_code(overrides, code)
        if code_ov:
            lines.append("- **相关覆写**:")
            for o in code_ov[:6]:
                lines.append(f"  - {o}")
        if final.get("rationale"):
            lines.append(f"- **终局理由**: {_one_line(final.get('rationale'), None)}")
        if final.get("time_horizon") or rec.get("time_horizon"):
            lines.append(f"- **周期**: {final.get('time_horizon') or rec.get('time_horizon')}")
        if final.get("target_price") is not None or rec.get("target_price") is not None:
            lines.append(
                f"- **目标价**: {final.get('target_price') if final.get('target_price') is not None else rec.get('target_price')}"
            )
        if final.get("stop_loss") is not None or rec.get("stop_loss") is not None:
            lines.append(
                f"- **止损**: {final.get('stop_loss') if final.get('stop_loss') is not None else rec.get('stop_loss')}"
            )
        sector = str(final.get("sector_tag") or rec.get("sector_tag") or infer_sector(code) or "")
        if sector:
            lines.append(f"- **板块**: {sector}")
        if final.get("key_risk") or rec.get("key_risk"):
            lines.append(f"- **主要风险**: {final.get('key_risk') or rec.get('key_risk')}")
        if final.get("invalidation") or rec.get("invalidation"):
            lines.append(f"- **失效条件**: {final.get('invalidation') or rec.get('invalidation')}")
        inv = final.get("invalidation_check") or rec.get("invalidation_check")
        if isinstance(inv, dict) and inv.get("invalidated"):
            lines.append(
                "- ⚠ 失效已触发: " + "；".join(str(x) for x in (inv.get("fired") or [])[:3])
            )
        lines.append("")

    return lines


def render_action_index_section(result: dict[str, Any]) -> list[str]:
    """详细论证 A3：动作索引（不重复结论卡逐票列表）。"""
    lines: list[str] = []
    summary = result.get("decision_summary") or {}
    lines.append("#### A3. 动作：怎么做（索引 · 核对结论卡 A3）")
    lines.append("")
    lines.append(
        "_核对结论卡 **A3**（含全文理由）。逐票推理与止损/目标/失效见下方 **B2 · ④**；"
        "模拟账本见同日 `*-sim.md`。_"
    )
    lines.append("")
    basis = summary.get("holdings_basis") or {}
    if basis.get("is_empty"):
        lines.append(
            "> **持仓基准**：声明真实持仓为空（空仓）。A3 的 buy/watch 不是模拟盘状态。"
        )
        lines.append("")
    elif basis.get("codes"):
        codes = "、".join(str(c) for c in basis["codes"])
        lines.append(
            f"> **持仓基准**：声明真实持仓 {codes}（与模拟组合无关）。"
        )
        lines.append("")
    if summary.get("sentiment_regime_note"):
        lines.append(f"- **舆情环境**: {summary['sentiment_regime_note']}")
    if summary.get("tail_risk_note"):
        lines.append(f"- **尾部/侧栏对仓位**: {summary['tail_risk_note']}")
    if summary.get("portfolio_summary"):
        lines.append(f"- **④终局组合摘要**: {summary['portfolio_summary']}")
    recs = result.get("recommendations") or []
    if recs:
        names = _stock_name_map(result)
        bits = []
        for rec in recs:
            code = str(rec.get("code") or "")
            action = str(rec.get("action") or "watch")
            label = _ACTION_LABEL.get(action, action)
            name = names.get(code, "")
            bits.append(f"`{code}`{name}→{label}")
        lines.append("- **终局一览**: " + "；".join(bits))
    else:
        lines.append("- _（本轮无结构化建议）_")
    lines.append("")
    return lines


def render_conclusion_card(result: dict[str, Any]) -> list[str]:
    """结论卡：主结论（分析→预测→动作）→ 推理链（宏观/板块 + 个股决策链）→ 侧栏。"""
    lines: list[str] = []
    market = (result.get("market") or {}).get("analysis") or {}
    digest = (result.get("intelligence") or {}).get("digest") or {}
    summary = result.get("decision_summary") or {}
    names = _stock_name_map(result)
    by_sec = _recs_by_sector(result)
    sectors = result.get("sectors") or []
    recs = result.get("recommendations") or []

    phase = market.get("phase_label") or market.get("phase") or "-"
    style = market.get("style_label") or market.get("style") or "-"
    risk = market.get("risk_level") or "-"
    conf = market.get("confidence", "-")
    driver = market.get("primary_driver") or "-"
    alloc = market.get("sector_allocation_hint") or "-"

    lines.append("## 结论卡（速读）")
    lines.append("")
    lines.append(
        "_阅读顺序：**A 主结论**（A1 分析→A2 预测→A3 动作）→ **B 推理链**（B1 宏观/板块 + B2 个股①–④）→ "
        "**C 侧栏**（争议/尾部，须确认才升权）。下方详细论证 A–C → D 趋势；"
        "复盘与模拟账本见同日独立小报告。后果自负，仅供参考。_"
    )
    lines.append("")

    dq = result.get("data_quality") or {}
    screen = result.get("screen") or {}
    if dq.get("degraded") or dq.get("screen_degraded") or screen.get("degraded"):
        warn = dq.get("screen_note") or screen.get("plain_note") or dq.get("note") or "数据/遴选降级"
        lines.append(f"> ⚠️ **可信度警告**: {warn}")
        lines.append("")
    if (result.get("decision_summary") or {}).get("holdings_basis", {}).get("is_empty"):
        lines.append(
            "> **持仓说明**: 本轮按**空仓**决策（`holdings` 未声明或为空）。"
            "深度池来自自动量化遴选，与模拟盘无关。"
        )
        lines.append("")

    # ---------- A. 主结论 ----------
    lines.append("### A. 主结论")
    lines.append("")
    lines.append(
        "_A 回答「看什么→预期什么→怎么做」。A3 动作来自决策链 **④风控终局**；"
        "完整①–④见下方 **B2**。_"
    )
    lines.append("")
    lines.append("#### A1. 分析：现在怎么看")
    lines.append("")
    lines.append(
        "_**本步做什么**：综合情报、宏观/流动性、微观结构与矛盾点，"
        "给出当前市场环境与配置倾向（描述现状，不下单）。_"
    )
    lines.append("")
    lines.append(f"- **环境**: {phase} · 风格 {style} · 风险 {risk} · 置信度 {conf}")
    if driver and driver != "-":
        lines.append(f"- **主驱动**: {_one_line(driver, 100)}")
    lines.append(f"- **配置倾向**: {alloc}")
    gl = (
        ((result.get("intelligence") or {}).get("macro_raw") or {}).get("global_liquidity")
        or {}
    )
    if gl.get("stance") and gl.get("stance") != "unknown":
        us10 = (gl.get("us_10y") or {}).get("latest")
        lines.append(
            f"- **全球流动性**: `{gl.get('stance')}`"
            + (f" · 美债10Y {us10}%" if us10 is not None else "")
            + f" — {_one_line(gl.get('a_share_implication') or gl.get('plain_note'), 90)}"
        )
    liq = market.get("liquidity_assessment") or {}
    if liq.get("global_liquidity_note"):
        lines.append(f"- **流动性解读**: {_one_line(liq.get('global_liquidity_note'), 100)}")
    micro = result.get("market_microstructure") or market.get("market_microstructure") or {}
    if micro.get("regime") and micro.get("regime") != "normal":
        lines.append(
            f"- **微观结构**: `{micro.get('regime')}` · "
            f"传导{'受扰' if not micro.get('fundamental_channel_ok', True) else '大致可用'}"
            f" — {_one_line(micro.get('implication') or micro.get('plain_note'), 90)}"
        )
    elif market.get("microstructure_note"):
        lines.append(f"- **微观结构**: {_one_line(market.get('microstructure_note'), 100)}")
    facts: list[str] = []
    for theme in (digest.get("headline_themes") or [])[:2]:
        facts.append(_one_line(theme, 80))
    for c in (market.get("contradictions") or [])[:2]:
        facts.append(f"矛盾：{_one_line(c, 70)}")
    for flag in (digest.get("risk_flags") or [])[:1]:
        facts.append(f"风险：{_one_line(flag, 70)}")
    if not facts and market.get("summary"):
        facts.append(_one_line(market["summary"], 100))
    for f in facts[:4]:
        lines.append(f"- {f}")
    lines.append("")

    lines.append("#### A2. 预测：接下来怎么预期")
    lines.append("")
    lines.append(
        "_**本步做什么**：在 A1 基础上给出主情景、主要风险与认错条件；"
        "并对照上周（延续/转向），仍属预期层。_"
    )
    lines.append("")
    outlook = summary.get("market_context") or market.get("summary") or ""
    if outlook:
        lines.append(f"- **主情景**: {_one_line(outlook, None)}")
    inv = [str(x) for x in (market.get("invalidation") or []) if str(x).strip()]
    if not inv:
        seen: set[str] = set()
        for rec in recs:
            raw = rec.get("invalidation")
            if not raw:
                continue
            s = str(raw).strip()
            if s and s not in seen:
                seen.add(s)
                inv.append(s)
    risks = [str(r) for r in (digest.get("risk_flags") or []) if str(r).strip()]
    if risks:
        lines.append(f"- **主要风险**: {'；'.join(_one_line(r, None) for r in risks)}")
    if inv:
        lines.append(f"- **若出现则认错**: {'；'.join(_one_line(x, None) for x in inv)}")
    vs = market.get("vs_prior") or {}
    if vs.get("continuity"):
        changed = "；".join(str(x) for x in (vs.get("what_changed") or []))
        lines.append(
            f"- **相对上周**: {vs.get('continuity')}"
            + (f" — {_one_line(changed, None)}" if changed.strip() else "")
        )
    lines.append("")

    lines.append("#### A3. 动作：怎么做（④风控终局）")
    lines.append("")
    lines.append(
        "_**本步做什么**：只列**可执行终局动作**（来自 B2④）。"
        "不是①研究评级列表；研究看好但未进组合/被风控压掉的票不会出现在这里。_"
    )
    lines.append("")
    basis = (result.get("decision_summary") or {}).get("holdings_basis") or {}
    if basis.get("is_empty"):
        lines.append("_以下动作基于你声明的**真实持仓：空仓**（与模拟盘无关）；以④终局为准。_")
    elif basis.get("codes"):
        codes = "、".join(str(c) for c in basis["codes"][:8])
        lines.append(f"_以下动作基于你声明的**真实持仓**：{codes}（与模拟盘无关）；以④终局为准。_")
    else:
        lines.append("_以下为面向你声明持仓的操作建议（④终局）；模拟盘见同日 `*-sim.md`。_")
    lines.append("")
    if not recs:
        lines.append("- （本轮无结构化建议）")
    else:
        for rec in recs:
            code = str(rec.get("code") or "")
            action = str(rec.get("action") or "watch")
            label = _ACTION_LABEL.get(action, action)
            name = names.get(code, "")
            pos = rec.get("position_pct")
            pos_s = f" · 仓位 {pos}%" if pos is not None else ""
            conf_s = rec.get("confidence", "-")
            # 理由全文，不截断（仅压空白）
            why = " ".join(str(rec.get("rationale") or "").split())
            sector = rec.get("sector_tag") or infer_sector(code) or ""
            sec_s = f" · 板块:{sector}" if sector else ""
            head = (
                f"- **{label}** {code}{(' ' + name) if name else ''} "
                f"(置信度 {conf_s}{pos_s}{sec_s})"
            )
            if why:
                lines.append(f"{head}")
                lines.append(f"  - 理由: {why}")
            else:
                lines.append(head)
    lines.append("")

    # ---------- B. 推理链（一体两层）----------
    lines.append("### B. 推理链（宏观→板块 → 个股决策链）")
    lines.append("")
    lines.append(
        "_B 解释 A 怎么来的：**B1** 先定宏观→板块骨架；"
        "**B2** 再对深度池走 ①研究→②组合草案（含综合）→③辩论→④风控。"
        "①研究评级 ≠ 开仓；可执行只看④与上方 **A3**。_"
    )
    lines.append("")

    lines.append("#### B1. 宏观 → 板块")
    lines.append("")
    lines.append(
        "_**本步做什么**：把情报主题落到市场阶段/风格/风险，再给出关注赛道的态度"
        "（优先/回避/跟踪），作为个股决策的上层约束。_"
    )
    lines.append("")
    theme0 = ""
    themes = digest.get("headline_themes") or []
    if themes:
        theme0 = _one_line(themes[0], None)
    elif digest.get("market_narratives"):
        theme0 = _one_line(digest["market_narratives"][0], None)
    head = f"情报「{theme0 or '（见 A1）'}」→ 市场「{phase} / {style}」(风险{risk})"
    lines.append(f"1. {head} → 配置倾向「{alloc}」")
    lines.append("")
    if not sectors:
        lines.append("- （无板块筛选）")
    else:
        lines.append("**赛道态度**")
        lines.append("")
        for sec in sectors:
            a = sec.get("analysis") or {}
            name = str(a.get("sector") or sec.get("sector") or "")
            if not name:
                continue
            related = by_sec.get(name) or []
            stance = _sector_stance(a, related)
            pri = a.get("priority", "-")
            bits = [
                f"政策:{a.get('policy_wind', '-')}",
                f"景气:{a.get('prosperity', '-')}",
                f"估值:{a.get('valuation', '-')}",
            ]
            crowd = (a.get("sentiment") or {}).get("crowding_risk")
            if crowd:
                bits.append(f"拥挤:{crowd}")
            link = ""
            if related:
                parts = []
                for r in related:
                    c = str(r.get("code") or "")
                    parts.append(f"{c}{_ACTION_LABEL.get(str(r.get('action')), r.get('action'))}")
                link = " → " + "、".join(parts)
            lines.append(
                f"- **{name}** [{pri}] {' · '.join(bits)} — **{stance}**{link}"
            )
    lines.append("")

    lines.append("#### B2. 个股决策链（①研究→②草案→③辩论→④风控）")
    lines.append("")
    lines.append(
        "_**本步做什么**：展示从「单票研究」到「可执行动作」的漏斗。"
        "下面「步骤说明」写清每列含义；尤其 **②=双分析师草案经综合后的组合取舍**，"
        "不是①的简单汇总。_"
    )
    lines.append("")
    stage_block = render_decision_stages_section(result)
    if stage_block:
        # 降级标题层级，并去掉外层「决策流程」二级标题（已由 B2 承接）
        for ln in stage_block:
            # 只降一级「### xxx」；勿误伤 #### / #####
            if ln.startswith("## ") and not ln.startswith("###"):
                continue
            if ln.startswith("### ") and not ln.startswith("####"):
                lines.append("##### " + ln[4:])
            else:
                lines.append(ln)
    else:
        lines.append("_本轮未写入 decision_stages（旧报告）；以结论卡 A3 / 详细论证 B2④ 为准。_")
        lines.append("")

    # ---------- C. 侧栏 ----------
    contested = _render_contested_block(
        result, heading="### C. 侧栏（争议叙事 / 尾部情景）"
    )
    if contested:
        lines.extend(contested)
    else:
        lines.append("### C. 侧栏（争议叙事 / 尾部情景）")
        lines.append("")
        lines.append("_（本轮无侧栏争议叙事）_")
        lines.append("")

    lines.append("---")
    lines.append("")
    return lines


def render_daily_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    run_date = result.get("run_date", date.today().isoformat())
    lines.append("# money_more 中长线周期决策报告")
    lines.append("")
    lines.append(f"**日期**: {run_date}")
    horizon = result.get("investment_horizon") or "medium_long"
    cadence = result.get("schedule_cadence") or ""
    lines.append(f"**取向**: 中长线（{horizon}）" + (f" · 节奏 `{cadence}`" if cadence else ""))
    lines.append("")

    from money_more.analysis.data_sources_ledger import render_data_sources_section

    lines.extend(render_data_sources_section(result))

    ma = result.get("multi_agent") or {}
    if ma.get("enabled"):
        meta = ma.get("meta") or {}
        if isinstance(meta, dict):
            lines.append(
                f"**多Agent决策**: {meta.get('primary', '?')} + {meta.get('secondary', '?')} "
                f"→ 综合 {meta.get('synthesizer', '?')}"
            )
        else:
            lines.append(f"**多Agent决策**: {meta}")
        if ma.get("draft_agents"):
            lines.append(f"- 独立草案: {', '.join(ma['draft_agents'])}")
        lines.append("")

    lines.extend(render_conclusion_card(result))

    digest = (result.get("intelligence") or {}).get("digest") or {}
    macro_intel = (result.get("intelligence") or {}).get("macro_raw") or {}
    sentiment_overview = macro_intel.get("sentiment_overview") or {}
    agg = sentiment_overview.get("aggregate") or {}

    lines.append("## 详细论证")
    lines.append("")
    lines.append(
        "_按结论卡 **A→B→C** 展开证据（不是第二份结论）。"
        "下列 A/B/C 为子节；其后 **D 趋势更新**。"
        "复盘与模拟账本见同日 `*-review.md` / `*-sim.md`。_"
    )
    lines.append("")

    market = (result.get("market") or {}).get("analysis") or {}
    radar = (result.get("intelligence") or {}).get("narrative_radar") or {}

    # ---------- A. 展开主结论 ----------
    lines.append("### A. 展开主结论（核对结论卡 A1–A3）")
    lines.append("")

    if digest or agg:
        lines.append("#### A1. 情报综述（展开分析）")
        lines.append("")
        if digest.get("executive_summary"):
            lines.append(digest["executive_summary"])
            lines.append("")
        if agg.get("score_100") is not None:
            lines.append(
                f"**量化舆情分**: {agg.get('score_100')}/100 ({agg.get('label', 'neutral')}) "
                f"· 样本 {agg.get('count', 0)} 条"
            )
        if digest.get("quant_sentiment_score_100") is not None:
            lines.append(f"**LLM 确认舆情分**: {digest.get('quant_sentiment_score_100')}")
        if digest.get("telegraph_highlights"):
            lines.append("")
            lines.append("**财联社快讯**:")
            for item in digest["telegraph_highlights"][:5]:
                lines.append(f"- {item}")
        if digest.get("headline_themes"):
            lines.append("**今日主题**: " + "；".join(digest["headline_themes"]))
        if digest.get("sentiment_temperature"):
            lines.append(f"**舆情温度**: {digest['sentiment_temperature']}")
        if digest.get("market_narratives"):
            lines.append("**市场叙事**: " + "；".join(digest["market_narratives"]))
        if digest.get("policy_signals"):
            lines.append("")
            lines.append("**政策信号**:")
            for sig in digest["policy_signals"][:5]:
                if isinstance(sig, dict):
                    lines.append(
                        f"- [{sig.get('direction', '?')}] {sig.get('signal', '')}"
                        f"（{sig.get('source', '')}）"
                    )
        if digest.get("risk_flags"):
            lines.append("")
            lines.append("**风险旗标**: " + "；".join(digest["risk_flags"]))
        lines.append("")

    lines.append("#### A2. 市场阶段与展望（展开预测）")
    lines.append("")
    lines.append(
        f"- **阶段**: {market.get('phase_label') or market.get('phase', 'unknown')} | "
        f"**风格**: {market.get('style_label') or market.get('style', 'unknown')}"
    )
    lines.append(
        f"- **风险**: {market.get('risk_level', 'unknown')} | "
        f"**置信度**: {market.get('confidence', '-')}"
    )
    if market.get("primary_driver"):
        lines.append(f"- **主驱动**: {market['primary_driver']}")
    if market.get("summary"):
        lines.append(f"- {market.get('summary')}")

    policy = market.get("policy_assessment") or {}
    if policy:
        lines.append(
            f"- 政策基调: {policy.get('tone', '-')} | "
            f"板块配置倾向: {market.get('sector_allocation_hint', '-')}"
        )

    sentiment = market.get("sentiment_assessment") or {}
    if sentiment:
        lines.append(
            f"- 舆情: {sentiment.get('level', '-')} | 叙事: {sentiment.get('narrative', '-')}"
        )
    if market.get("signals"):
        lines.append("- 信号: " + "；".join(market["signals"]))
    if market.get("contradictions"):
        lines.append("- ⚠️ 张力: " + "；".join(market["contradictions"]))
    vs = market.get("vs_prior") or {}
    if vs:
        changed = "；".join(str(x) for x in (vs.get("what_changed") or []))
        lines.append(
            f"- 跨日: {vs.get('continuity', '-')}"
            + (f" | 变化: {changed}" if changed else "")
        )

    gl = ((result.get("intelligence") or {}).get("macro_raw") or {}).get("global_liquidity") or {}
    if gl:
        lines.append("")
        lines.append("##### 全球流动性硬指标")
        lines.append("")
        lines.append(f"> {gl.get('plain_note') or '美债/汇率等外因硬指标。'}")
        lines.append("")
        lines.append(f"- **stance**: `{gl.get('stance', '-')}`")
        us10 = gl.get("us_10y") or {}
        if us10:
            lines.append(
                f"- **美债10Y**: {us10.get('latest')}% · "
                f"Δ5d {us10.get('change_5d_bp')}bp · Δ20d {us10.get('change_20d_bp')}bp · "
                f"Δ60d {us10.get('change_60d_bp')}bp"
            )
        curve = gl.get("us_2s10s") or {}
        if curve.get("latest") is not None:
            lines.append(f"- **美债10Y-2Y**: {curve.get('latest')}")
        cn10 = gl.get("cn_10y") or {}
        if cn10.get("latest") is not None:
            lines.append(f"- **中国10Y**: {cn10.get('latest')}%")
        if gl.get("us_cn_10y_spread_bp") is not None:
            lines.append(f"- **中美10Y利差**: {gl.get('us_cn_10y_spread_bp')}bp（美-中）")
        fx = gl.get("usd_cny") or {}
        if fx.get("latest") is not None:
            lines.append(
                f"- **USD/CNY**: {fx.get('latest')} · Δ20d {fx.get('change_20d_pct')}%"
            )
        if gl.get("signals"):
            lines.append("- **信号**: " + "；".join(str(x) for x in gl["signals"][:4]))
        if gl.get("a_share_implication"):
            lines.append(f"- **对 A 股含义**: {gl['a_share_implication']}")
        liq = market.get("liquidity_assessment") or {}
        if liq.get("global_liquidity_note"):
            lines.append(f"- LLM: {_one_line(liq.get('global_liquidity_note'), None)}")
        lines.append("")

    micro = result.get("market_microstructure") or market.get("market_microstructure") or {}
    if micro:
        lines.append("")
        lines.append("##### 微观结构 / 流动性断点")
        lines.append("")
        lines.append(
            f"> {micro.get('plain_note') or ''} "
            "（机制信号可进入仓位纪律，与侧栏叙事指控区分。）"
        )
        lines.append("")
        lines.append(
            f"- **状态**: `{micro.get('regime', '-')}` · "
            f"基本面→价格传导: "
            f"{'可能受扰' if not micro.get('fundamental_channel_ok', True) else '大致可用'}"
        )
        if micro.get("implication"):
            lines.append(f"- **含义**: {micro['implication']}")
        if micro.get("flags"):
            lines.append("- **信号**: " + "；".join(str(x) for x in micro["flags"][:6]))
        m = micro.get("metrics") or {}
        if m:
            bits = []
            for k in (
                "up_ratio",
                "down_ratio",
                "median_abs_change_pct",
                "amount_top50_share",
                "limit_up_count",
                "limit_down_count",
                "index_abs_change_max",
            ):
                if m.get(k) is not None:
                    bits.append(f"{k}={m[k]}")
            if bits:
                lines.append("- **指标**: " + " · ".join(bits[:8]))
        if market.get("microstructure_note"):
            lines.append(f"- LLM: {_one_line(market.get('microstructure_note'), None)}")
        lines.append("")

    snap = (result.get("market") or {}).get("snapshot") or {}
    if snap.get("style_proxy") or snap.get("limit_up_count") is not None:
        sp = snap.get("style_proxy") or {}
        lines.append(
            f"- 风格代理: cyb-hs300={sp.get('cyb_vs_hs300_1d', '-')} | "
            f"涨停={snap.get('limit_up_count', '-')} 跌停={snap.get('limit_down_count', '-')}"
        )
        lines.append("")

    lines.extend(render_action_index_section(result))

    # ---------- B. 展开推理链 ----------
    lines.append("### B. 展开推理链（核对结论卡 B1–B2）")
    lines.append("")
    lines.append("#### B1. 板块筛选")
    lines.append("")
    su = result.get("sector_universe") or {}
    if su.get("note"):
        lines.append(f"> {su['note']}")
        lines.append("")
    if su.get("auto_sectors") or su.get("watch_sectors"):
        lines.append(
            f"- 关注板块: {'、'.join(su.get('watch_sectors') or []) or '—'}"
            f" · 资金流自动扩: {'、'.join(su.get('auto_sectors') or []) or '—'}"
        )
        lines.append("")
    by_sec = _recs_by_sector(result)
    sectors = result.get("sectors") or []
    if not sectors:
        lines.append("_（无板块筛选）_")
        lines.append("")
    for sec in sectors:
        a = sec.get("analysis") or {}
        sec_name = str(a.get("sector") or sec.get("sector") or "")
        worth = "✅" if a.get("worth_research") else "⏸"
        sent = _fmt_sentiment(a.get("sentiment"))
        quant = a.get("sentiment") or {}
        related = by_sec.get(sec_name) or []
        stance = _sector_stance(a, related)
        src = sec.get("source") or a.get("sector_source") or "watch"
        src_s = "自动扩" if src == "auto_flow" else "关注"
        lines.append(
            f"- {worth} **{sec_name}** "
            f"[{a.get('priority', '-')}优先级 · {src_s}] | "
            f"政策:{a.get('policy_wind')} 景气:{a.get('prosperity')} 估值:{a.get('valuation')}"
        )
        if quant.get("quant_score_100") is not None:
            lines.append(
                f"  - 量化舆情: {quant.get('quant_score_100')}/100 ({quant.get('quant_label', '-')})"
            )
        if sent:
            lines.append(f"  - 舆情: {sent}")
        lines.append(f"  - {a.get('summary', '')}")
        if a.get("narrative"):
            lines.append(f"  - 叙事: {a['narrative']}")
        link_bits = []
        for r in related:
            link_bits.append(
                f"{r.get('code')}→{_ACTION_LABEL.get(str(r.get('action')), r.get('action'))}"
            )
        link_s = ("；".join(link_bits) + "。") if link_bits else "深度池暂无对应个股动作。"
        lines.append(f"  - **落到动作**: {stance} — {link_s}")
    if sectors:
        lines.append("")

    screen = result.get("screen") or {}
    if screen:
        lines.append("##### 个股遴选漏斗")
        lines.append("")
        lines.append(
            f"> {screen.get('plain_note') or screen.get('note') or '量化遴选后进入深度分析。'}"
        )
        lines.append("")
        lines.append(
            "- **术语**: 量化池=打分入围；深度池=本轮 LLM 细读名单（自动遴选）；"
            "若有声明持仓则强制进深度池且**不占** `max_deep`。"
        )
        lines.append(
            f"- 宇宙来源: `{screen.get('universe_source') or screen.get('universe_mode')}` · "
            f"滤后 {screen.get('universe_size', '—')} · "
            f"量化入围 {screen.get('quant_size', '—')} · "
            f"深度分析 {screen.get('deep_size', '—')}"
            f"（新票 {screen.get('screened_added', '—')}）"
        )
        fs = screen.get("filter_stats") or {}
        if fs:
            lines.append(
                f"- 过滤统计: 剔除 ST={fs.get('st', 0)} · 低流动性={fs.get('illiquid', 0)} · "
                f"负PE硬剔={fs.get('neg_pe', 0)} · 高PE硬剔={fs.get('high_pe', 0)}"
                "（默认高PE/负PE不硬剔，只降权）"
            )
        forced = screen.get("force_codes") or screen.get("must_codes") or []
        if forced:
            lines.append(f"- **持仓强制进池**: {'、'.join(str(c) for c in forced)}")
        else:
            lines.append("- **持仓强制进池**: （空）深度池全部来自量化遴选")
        tops = screen.get("top_candidates") or []
        if tops:
            lines.append("- **量化前列**（进入深度池的优先候选）:")
            for t in tops[:10]:
                flag = " ·持仓" if (t.get("forced") or t.get("must")) else ""
                deep_flag = " ·深" if t.get("in_deep") else ""
                theme = t.get("theme") or t.get("sector") or ""
                theme_bit = f" ·{theme}" if theme else ""
                lines.append(
                    f"  - `{t.get('code')}` {t.get('name') or ''} "
                    f"分={t.get('screen_score')} PE={t.get('pe')} PB={t.get('pb')}"
                    f"{theme_bit}{deep_flag}{flag}"
                )
        conc = screen.get("theme_concentration") or {}
        theme_counts = conc.get("theme_counts") or {}
        if theme_counts:
            dist = "、".join(
                f"{k}×{v}" for k, v in sorted(theme_counts.items(), key=lambda x: -x[1])
            )
            lines.append(f"- **深度池主题分布**（中长线分散）: {dist}")
            top_share = conc.get("top_share")
            top_theme = conc.get("top_theme")
            cap = conc.get("max_deep_per_theme")
            if top_theme and top_share is not None:
                warn = ""
                if float(top_share) >= 0.5:
                    warn = " ·占比偏高，注意同质化风险"
                cap_bit = f" ·单主题上限{cap}" if cap else ""
                lines.append(
                    f"- **集中度**: 头号主题 `{top_theme}` 约占 {float(top_share):.0%}"
                    f"{cap_bit}{warn}"
                )
            floors = conc.get("floor_filled") or []
            if floors:
                lines.append(f"- **防御软保底**: {'、'.join(str(x) for x in floors)}")
        if screen.get("degraded") or not screen.get("ok", True):
            lines.append(f"- ⚠️ **遴选降级**: {screen.get('note')}")
        lines.append("")

    lines.extend(render_stock_decision_chains(result))

    # ---------- C. 展开侧栏 ----------
    lines.append("### C. 展开侧栏（核对结论卡 C）")
    lines.append("")
    lines.append(
        "_须确认/证伪信号才升权，**不得单独当买入理由**。"
        "结论卡 C 为摘要；以下为线索与假说展开。_"
    )
    lines.append("")

    contested = _render_contested_block(
        result,
        heading="#### 争议叙事 / 尾部情景",
        include_policy=False,
    )
    if contested:
        lines.extend(contested)
    else:
        lines.append("#### 争议叙事 / 尾部情景")
        lines.append("")
        lines.append("_（本轮无争议叙事条目）_")
        lines.append("")

    pol_scen = market.get("policy_market_scenario") or {}
    if pol_scen:
        lines.append("#### 政策市假说（护盘 / 出清）")
        lines.append("")
        lines.append(
            f"- **状态**: `{pol_scen.get('status', '-')}` · 来源 {pol_scen.get('source_type', '-')}"
        )
        if pol_scen.get("thesis"):
            lines.append(f"- **假说**: {_one_line(pol_scen.get('thesis'), None)}")
        if pol_scen.get("confirm_signals"):
            lines.append(
                "- **确认信号**: "
                + "；".join(_one_line(x, None) for x in pol_scen["confirm_signals"])
            )
        if pol_scen.get("falsify_signals"):
            lines.append(
                "- **证伪信号**: "
                + "；".join(_one_line(x, None) for x in pol_scen["falsify_signals"])
            )
        if pol_scen.get("implication"):
            lines.append(f"- **若成立**: {_one_line(pol_scen.get('implication'), None)}")
        if pol_scen.get("evidence_now"):
            lines.append(
                "- **本轮线索**: "
                + "；".join(_one_line(x, None) for x in pol_scen["evidence_now"])
            )
        if pol_scen.get("note"):
            lines.append(f"- _{pol_scen['note']}_")
        lines.append("")

    radar_assess = digest.get("narrative_radar_assessment") or []
    if radar or radar_assess:
        lines.append("#### 叙事雷达（线索扫描）")
        lines.append("")
        if radar:
            lines.append(
                f"> {radar.get('plain_note') or '规则扫描高争议/尾部叙事线索；侧栏用，非主剧本。'}"
            )
            lines.append("")
            for t in radar.get("tracks") or []:
                strength = t.get("signal_strength") or "none"
                if strength == "none":
                    lines.append(
                        f"- ○ **{t.get('title')}** — 本轮无线索（`{t.get('source_type', '-')}`）"
                    )
                    continue
                lines.append(
                    f"- ● **{t.get('title')}** [{strength}] · "
                    f"{t.get('source_type', '-')} · 命中 {t.get('hit_count', 0)}"
                )
                for snip in t.get("evidence_snippets") or []:
                    lines.append(f"  - {_one_line(snip, None)}")
        if radar_assess:
            lines.append("")
            lines.append("**LLM 雷达评估**:")
            for item in radar_assess:
                if isinstance(item, dict):
                    lines.append(
                        f"- [{item.get('stance', '?')}] "
                        f"{item.get('title') or item.get('track_id')} "
                        f"· {item.get('source_type', '-')} — "
                        f"{_one_line(item.get('why'), None)}"
                    )
        lines.append("")


    # ---------- D. 趋势（复盘/模拟已拆为独立小报告）----------
    trend = result.get("trend") or {}
    if trend:
        lines.append("## D. 趋势更新（滚动）")
        lines.append("")
        regime = trend.get("market_regime") or {}
        lines.append(
            f"- **当前阶段**: {regime.get('current_label') or regime.get('current_phase', '-')} | "
            f"风格:{regime.get('current_style', '-')} | 风险:{regime.get('risk_level', '-')}"
        )
        if regime.get("regime_change"):
            lines.append(
                f"- **regime 变化**: {regime.get('regime_change')} — {regime.get('change_note', '')}"
            )
        if trend.get("executive_summary"):
            lines.append(f"- {trend['executive_summary']}")
        sent_t = trend.get("sentiment_trend") or {}
        if sent_t:
            lines.append(
                f"- 舆情趋势: {sent_t.get('direction', '-')} "
                f"(最新分 {sent_t.get('latest_score_100', '-')})"
            )
        lines.append("")
        lines.append("_完整滚动趋势见 `reports/trend.md`_")
        lines.append("")

    dq = result.get("data_quality") or {}
    if dq:
        lines.append(
            f"**数据质量分**: {dq.get('score', '-')} · {dq.get('note', '')}"
            "（明细见文首「数据源说明」）"
        )
        lines.append("")

    digest = result.get("decision_digest") or {}
    if digest:
        lines.append(
            f"**决策摘要**: phase={digest.get('market_phase')} style={digest.get('market_style')} "
            f"risk={digest.get('risk_level')} prompt={digest.get('prompt_version')} "
            f"overrides={digest.get('validation_override_count')} risk_ok={digest.get('risk_check_ok')}"
        )
        lines.append("")

    run_date = result.get("run_date", date.today().isoformat())
    lines.append(
        f"_同日独立报告：[`{run_date}-review.md`]({run_date}-review.md)（复盘与经验）· "
        f"[`{run_date}-sim.md`]({run_date}-sim.md)（模拟账本）。_"
    )
    lines.append("")

    lines.append("---")
    lines.append("*本报告由 AI 生成，仅供参考，不构成投资建议。*")

    return "\n".join(lines)


def render_review_report(result: dict[str, Any]) -> str:
    """独立小报告：复盘与经验。"""
    lines: list[str] = []
    run_date = result.get("run_date", date.today().isoformat())
    lines.append("# money_more 复盘与经验")
    lines.append("")
    lines.append(f"**日期**: {run_date}")
    lines.append("")
    lines.append("_与主报告分离；邮件不附送。浮盈亏只作轨迹，不等于预测成败。_")
    lines.append("")

    rw = result.get("review_window") or {}
    rw_note = result.get("review_window_note") or ""
    if rw or rw_note:
        lookback = rw.get("lookback_days")
        cutoff = rw.get("cutoff")
        as_of = rw.get("as_of")
        lines.append(
            f"> **取材窗口**：近 {lookback or '—'} 日"
            + (f"（{cutoff} → {as_of}）" if cutoff and as_of else "")
            + "。开放式预测下，**浮盈亏只作轨迹，不等于预测成败**。"
        )
        if rw_note:
            lines.append(f"> {rw_note}")
        lines.append("")

    dim_reviews = result.get("dimension_reviews") or []
    dim_labels = {
        "market": "市场阶段",
        "sector": "板块优先级",
        "narrative": "叙事 / 情报",
        "linkage": "逻辑链（板块→个股→动作）",
    }
    if dim_reviews:
        lines.append("## 维度复盘（全漏斗）")
        lines.append("")
        by_dim: dict[str, list] = {}
        for dr in dim_reviews:
            by_dim.setdefault(str(dr.get("dimension") or "other"), []).append(dr)
        for key in ("market", "sector", "narrative", "linkage"):
            group = by_dim.pop(key, [])
            if not group:
                continue
            lines.append(f"### {dim_labels.get(key, key)}")
            lines.append("")
            for dr in group:
                subject = dr.get("subject") or ""
                outcome = dr.get("outcome") or "pending"
                as_of_f = dr.get("as_of_forecast") or ""
                pq = dr.get("process_quality") or ""
                cat = dr.get("diagnosis_category") or ""
                lines.append(
                    f"- **{subject}** → `{outcome}`"
                    + (f" · 预测:{as_of_f}" if as_of_f else "")
                    + (f" · 过程:{pq}" if pq and pq != "unclear" else "")
                    + (f" · [{cat}]" if cat else "")
                )
                if dr.get("diagnosis"):
                    lines.append(f"  - {dr['diagnosis']}")
                if dr.get("what_worked"):
                    lines.append(f"  - 做对: {'; '.join(str(x) for x in dr['what_worked'][:3])}")
                if dr.get("what_failed"):
                    lines.append(f"  - 做错: {'; '.join(str(x) for x in dr['what_failed'][:3])}")
                if dr.get("lesson"):
                    lines.append(f"  - 经验: {dr['lesson']}")
            lines.append("")
        for key, group in by_dim.items():
            lines.append(f"### {key}")
            lines.append("")
            for dr in group:
                lines.append(
                    f"- **{dr.get('subject') or '?'}** → `{dr.get('outcome') or 'pending'}`"
                )
                if dr.get("diagnosis"):
                    lines.append(f"  - {dr['diagnosis']}")
            lines.append("")

    reviews = result.get("reviews") or []
    lines.append("## 个股动作复盘（thesis / 失效 / 纪律 / 轨迹）")
    lines.append("")
    if not reviews:
        lines.append("_本轮无新增个股复盘（未满观察期，或暂无可跟踪建议）_")
    else:
        for rv in reviews:
            status = rv.get("status") or rv.get("outcome") or "tracking"
            cat = rv.get("diagnosis_category") or ""
            ret = rv.get("return_pct")
            ret_s = f"{ret}%" if ret is not None else "—"
            lines.append(
                f"- **{rv.get('stock_code')}** status=`{status}` · 轨迹收益:{ret_s}"
                + (f" · [{cat}]" if cat else "")
            )
            pq = rv.get("process_quality")
            lq = rv.get("linkage_quality")
            disc = rv.get("discipline")
            bits = []
            if pq and pq != "unclear":
                bits.append(f"过程:{pq}")
            if lq and lq != "unclear":
                bits.append(f"链路:{lq}")
            if disc and disc != "n/a":
                bits.append(f"纪律:{disc}")
            if bits:
                lines.append(f"  - {' · '.join(bits)}")
            inv = rv.get("invalidation_check") or {}
            if inv.get("invalidated") or inv.get("fired"):
                lines.append(
                    f"  - 失效触发: {', '.join(str(x) for x in (inv.get('fired') or [])[:3])}"
                )
            if rv.get("diagnosis"):
                lines.append(f"  - {rv['diagnosis']}")
            if rv.get("lesson"):
                lines.append(f"  - 经验: {rv['lesson']}")
            if rv.get("prompt_adjustment"):
                lines.append(f"  - 分析改进: {rv['prompt_adjustment']}")
    if not dim_reviews and not reviews:
        lines.append("")
        lines.append(
            "_提示：复盘对照近 60 日报告的市场/板块/叙事/链路与个股 thesis；"
            "满观察期后更新 tracking，不以浮亏单独结案。_"
        )
    lines.append("")

    patterns = result.get("history_patterns") or []
    meta = result.get("meta_lessons") or []
    sentiment_lessons = result.get("sentiment_lessons") or []
    lessons_used = result.get("lessons_used") or []
    if patterns or meta or sentiment_lessons or lessons_used:
        lines.append("## 经验库")
        lines.append("")
        for item in patterns:
            lines.append(f"- 🔁 [pattern] {item}")
        for item in meta:
            lines.append(f"- 🆕 {item}")
        for item in sentiment_lessons:
            lines.append(f"- 📢 [舆情] {item}")
        seen = set()
        for item in lessons_used[:20]:
            content = (item.get("content") or "").strip()
            key = content[:80]
            if not content or key in seen:
                continue
            seen.add(key)
            lines.append(f"- 📚 [{item.get('category')}] {content}")
            if len(seen) >= 10:
                break
        lines.append("")

    lines.append("---")
    lines.append("*复盘小报告 · 仅供参考，不构成投资建议。*")
    return "\n".join(lines)


def render_sim_report(result: dict[str, Any]) -> str:
    """独立小报告：模拟账本（非真实持仓）。"""
    from money_more.sim.engine import render_sim_section

    run_date = result.get("run_date", date.today().isoformat())
    lines: list[str] = [
        "# money_more 模拟账本",
        "",
        f"**日期**: {run_date}",
        "",
        "_与主报告分离；邮件不附送。评估「若完全按结论卡 A3 终局执行」的效果。_",
        "",
    ]
    body = render_sim_section(result.get("sim_portfolio"), result=result)
    if not body:
        lines.append("_本轮无模拟账本（未启用或已跳过）。_")
        lines.append("")
    else:
        lines.extend(body)
    lines.append("---")
    lines.append("*模拟小报告 · 非真实持仓 · 仅供参考。*")
    return "\n".join(lines)



def render_trend_report(trend: dict[str, Any]) -> str:
    lines: list[str] = []
    as_of = trend.get("as_of", date.today().isoformat())
    lines.append("# money_more 滚动趋势报告")
    lines.append("")
    lines.append(f"**截至**: {as_of}  ·  **更新于**: {trend.get('updated_at', '-')}")
    lines.append("")

    if trend.get("executive_summary"):
        lines.append("## 总览")
        lines.append("")
        lines.append(trend["executive_summary"])
        lines.append("")

    regime = trend.get("market_regime") or {}
    lines.append("## 市场 Regime")
    lines.append("")
    lines.append(
        f"- 阶段: **{regime.get('current_label') or regime.get('current_phase', '-')}**"
    )
    lines.append(f"- 风格: {regime.get('current_style', '-')} | 风险: {regime.get('risk_level', '-')}")
    lines.append(f"- 主驱动: {regime.get('primary_driver', '-')}")
    lines.append(f"- 配置倾向: {regime.get('allocation_hint', '-')}")
    lines.append(
        f"- Regime 变化: {regime.get('regime_change', 'none')} — {regime.get('change_note', '')}"
    )
    lines.append("")

    sent = trend.get("sentiment_trend") or {}
    liq = trend.get("liquidity_trend") or {}
    lines.append("## 舆情与流动性")
    lines.append("")
    lines.append(
        f"- 舆情: {sent.get('direction', '-')} · 最新分 {sent.get('latest_score_100', '-')} · {sent.get('note', '')}"
    )
    lines.append(
        f"- 流动性: 两融 {liq.get('margin', '-')} · 北向 {liq.get('northbound', '-')} · {liq.get('note', '')}"
    )
    lines.append("")

    series = trend.get("market_series") or []
    if series:
        lines.append("## 市场序列（近 15 日）")
        lines.append("")
        lines.append("| 日期 | 阶段 | 风格 | 风险 | 舆情分 | 主驱动 |")
        lines.append("|------|------|------|------|--------|--------|")
        for row in series[-15:]:
            lines.append(
                f"| {row.get('date','')} | {row.get('phase','')} | {row.get('style','')} | "
                f"{row.get('risk','')} | {row.get('sentiment_score','')} | {row.get('driver','') or ''} |"
            )
        lines.append("")

    sectors = trend.get("sector_trends") or []
    if sectors:
        lines.append("## 板块趋势")
        lines.append("")
        for s in sectors:
            lines.append(
                f"- **{s.get('sector')}** [{s.get('status','-')}] "
                f"政策:{s.get('policy_wind','-')} 景气:{s.get('prosperity','-')}"
            )
            if s.get("narrative"):
                lines.append(f"  - 叙事: {s['narrative']}")
        lines.append("")

    stocks = trend.get("stock_trends") or []
    if stocks:
        lines.append("## 个股趋势")
        lines.append("")
        for s in stocks:
            path = " → ".join(str(x) for x in (s.get("rating_path") or [])[-5:])
            lines.append(
                f"- **{s.get('code')}** {s.get('name','')} [{s.get('status','-')}] "
                f"评级路径: {path or '-'}"
            )
            if s.get("thesis"):
                lines.append(f"  - 逻辑: {s['thesis']}")
        lines.append("")

    narrative = trend.get("narrative_log") or []
    if narrative:
        lines.append("## 叙事日志（近 20 条）")
        lines.append("")
        for item in narrative[-20:]:
            lines.append(
                f"- **{item.get('date')}**: {item.get('headline','')} "
                f"（Δ {item.get('delta','')}）"
            )
        lines.append("")

    if trend.get("open_questions"):
        lines.append("## 待验证问题")
        lines.append("")
        for q in trend["open_questions"][:8]:
            if isinstance(q, dict):
                status = q.get("status") or "open"
                text = q.get("text") or ""
                lines.append(
                    f"- [{status}] {text} "
                    f"(opened {q.get('opened_on', '-')}, last {q.get('last_confirmed', '-')})"
                )
            else:
                lines.append(f"- {q}")
        lines.append("")

    if trend.get("watch_items"):
        lines.append("## 观察清单")
        lines.append("")
        for w in trend["watch_items"][:8]:
            if isinstance(w, dict):
                lines.append(f"- {w.get('event') or w}")
            else:
                lines.append(f"- {w}")
        lines.append("")

    lines.append("---")
    lines.append("*滚动维护：每日 `money-more run` 自动更新。仅供参考，不构成投资建议。*")
    return "\n".join(lines)


def save_report(result: dict[str, Any], reports_dir: Path) -> Path:
    """写入主报告 + 复盘/模拟小报告；返回主报告路径。"""
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = result.get("run_date", date.today().isoformat())
    md_path = reports_dir / f"{run_date}.md"
    json_path = reports_dir / f"{run_date}.json"
    review_path = reports_dir / f"{run_date}-review.md"
    sim_path = reports_dir / f"{run_date}-sim.md"

    md_path.write_text(render_daily_report(result), encoding="utf-8")
    review_path.write_text(render_review_report(result), encoding="utf-8")
    sim_path.write_text(render_sim_report(result), encoding="utf-8")
    json_path.write_text(dumps_json(result, indent=2), encoding="utf-8")

    digest = result.get("decision_digest")
    if digest:
        dig_dir = reports_dir / "digests"
        dig_dir.mkdir(parents=True, exist_ok=True)
        (dig_dir / f"{run_date}.json").write_text(dumps_json(digest, indent=2), encoding="utf-8")

    trend = result.get("trend") or {}
    if trend:
        save_trend_report(trend, reports_dir)

    result["report_paths"] = {
        "main": str(md_path),
        "review": str(review_path),
        "sim": str(sim_path),
    }
    return md_path


def save_trend_report(trend: dict[str, Any], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / "trend.md"
    json_path = reports_dir / "trend.json"
    md_path.write_text(render_trend_report(trend), encoding="utf-8")
    json_path.write_text(dumps_json(trend, indent=2), encoding="utf-8")
    return md_path