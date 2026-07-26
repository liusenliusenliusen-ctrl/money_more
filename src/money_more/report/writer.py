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
) -> list[str]:
    """争议叙事 / 尾部情景侧栏（信号与含义全文，不截断）。"""
    market = (result.get("market") or {}).get("analysis") or {}
    summary = result.get("decision_summary") or {}
    items = list(market.get("contested_narratives") or summary.get("contested_narratives") or [])
    pol = market.get("policy_market_scenario") or summary.get("policy_market_scenario") or {}
    if not items and not pol:
        return []
    lines = [heading, ""]
    lines.append(
        "_【侧栏语气】下列高争议/尾部情景：须确认信号出现才升权，"
        "不得单独作为买入理由。来源：硬数据 / 市场定价 / 网络叙事。"
        "与上方【主结论】分层阅读。_"
    )
    lines.append("")
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
    return f"{label}{(' ' + pct_s) if pct_s else ''}{conf_s}{extra}"


def render_decision_stages_section(result: dict[str, Any]) -> list[str]:
    """展示 研究→草案→辩论→风控 分阶段结论。"""
    stages = result.get("decision_stages") or {}
    if not stages:
        return []
    lines: list[str] = []
    lines.append("## 决策流程（分阶段结论）")
    lines.append("")
    lines.append(
        "_流程固定为：**①个股研究 → ②组合草案 → ③多空辩论 → ④风控终局**。"
        "下表为个股细化一览；**按票完整推理见 §3**；§4 / 上方 A 动作只列④终局指令。"
        "只有④的 buy/add（仓位>0）可执行并进入模拟盘；①的研究评级 buy ≠ 开仓指令。_"
    )
    lines.append("")
    flow = stages.get("flow") or []
    if flow:
        lines.append("**本轮步骤**: " + " → ".join(str(x) for x in flow))
        lines.append("")

    summary = result.get("decision_summary") or {}
    final_sum = stages.get("final_portfolio_summary") or summary.get("portfolio_summary") or ""
    draft_sum = stages.get("draft_portfolio_summary") or summary.get("portfolio_summary_draft") or ""
    # 先草案、后终局，便于对照「被覆盖前」与「可执行后」
    if draft_sum and draft_sum.strip() and draft_sum.strip() != str(final_sum).strip():
        lines.append(
            f"**②草案摘要（已被终局覆盖，仅供对照）**: {_one_line(draft_sum, None)}"
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
    """§3：每只票完整决策链（研究→草案→辩论→风控）。"""
    lines: list[str] = []
    lines.append("## 3. 个股决策链（①研究→②草案→③辩论→④风控）【主结论层】")
    lines.append("")
    lines.append(
        "_每只票写完整推理链；**①研究评级 ≠ 可开仓指令**。"
        "§4 只承接各票的 **④风控终局** 动作。_"
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

        lines.append(f"### {code}{(' ' + name) if name else ''}")
        lines.append("")
        lines.append(f"**决策链**: {bridge}")
        lines.append("")

        # ① 研究
        lines.append("#### ① 研究（基本面 / 赔率 / 叙事）")
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
        lines.append("#### ② 组合草案")
        lines.append("")
        d = draft_by.get(code)
        if d:
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
        lines.append("#### ③ 多空辩论")
        lines.append("")
        debate = rec.get("debate") or debates.get(code) or {}
        ds = debate_stage_by.get(code) or {}
        status = rec.get("debate_status") or ds.get("debate_status")
        if debate and not debate.get("error"):
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
        lines.append("#### ④ 风控终局")
        lines.append("")
        final = rec or risk_by.get(code) or {}
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
            lines.append(f"- **终局理由**: {_one_line(final.get('rationale'), 160)}")
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


def render_recommendations_section(result: dict[str, Any]) -> list[str]:
    """§4：承接 §3 ④终局，只列可执行指令。"""
    lines: list[str] = []
    summary = result.get("decision_summary") or {}
    lines.append("## 4. 买卖建议【主结论层】")
    lines.append("")
    lines.append(
        "> 本节**承接 §3 各票的④风控终局**，只列可执行指令；"
        "完整推理链（研究/草案/辩论/风控）见 §3。争议叙事见结论卡/§1【侧栏】；模拟账本见文末附录。"
    )
    lines.append("")
    basis = summary.get("holdings_basis") or {}
    if basis.get("is_empty"):
        lines.append(
            "> **持仓基准**：你声明的真实持仓为空（空仓）。"
            "下列 buy/watch 是「若按本轮终局结论配置」的建议，**不是**模拟盘状态，也不是假设你已持有某票。"
        )
    elif basis.get("codes"):
        codes = "、".join(str(c) for c in basis["codes"])
        lines.append(
            f"> **持仓基准**：你声明的真实持仓 {codes}。"
            "hold/add/sell 针对上述持仓；buy/watch 针对尚未持有的标的。"
            "**与后文「模拟组合」无关**。"
        )
    else:
        lines.append("> **持仓基准**：以你声明的真实持仓为准；与后文模拟组合分离。")
    lines.append("")
    if summary.get("market_context"):
        lines.append(f"> {summary['market_context']}")
        lines.append("")
    if summary.get("sentiment_regime_note"):
        lines.append(f"**舆情环境**: {summary['sentiment_regime_note']}")
        lines.append("")
    if summary.get("tail_risk_note"):
        lines.append(f"**尾部/争议侧栏对仓位**: {summary['tail_risk_note']}")
        lines.append("")
    if summary.get("portfolio_summary"):
        lines.append(f"**④终局组合摘要**: {summary['portfolio_summary']}")
        lines.append("")

    action_emoji = {
        "buy": "🟢买入",
        "add": "🟢加仓",
        "sell": "🔴卖出",
        "hold": "🟡持有",
        "watch": "👀观察",
    }
    names = _stock_name_map(result)
    recs = result.get("recommendations") or []
    if not recs:
        lines.append("_（本轮无结构化建议）_")
        lines.append("")
        return lines

    for rec in recs:
        action = str(rec.get("action", "watch"))
        label = action_emoji.get(action, action)
        code = str(rec.get("code") or "")
        name = names.get(code, "")
        lines.append(f"### {label} {code}{(' ' + name) if name else ''}")
        lines.append("")
        lines.append(f"- **承接 §3**: {build_stock_chain_bridge(code, result, final_rec=rec)}")
        lines.append(f"- 置信度: {rec.get('confidence', '-')}")
        if rec.get("time_horizon"):
            lines.append(f"- 周期: {rec.get('time_horizon')}")
        if rec.get("position_pct") is not None:
            lines.append(f"- 建议仓位: {rec.get('position_pct')}%")
        if rec.get("target_price") is not None:
            lines.append(f"- 目标价: {rec.get('target_price')}")
        if rec.get("stop_loss") is not None:
            lines.append(f"- 止损: {rec.get('stop_loss')}")
        sector = str(rec.get("sector_tag") or infer_sector(code) or "")
        if sector:
            lines.append(f"- 板块: {sector}")
        if rec.get("rationale"):
            lines.append(f"- 指令要点: {_one_line(rec.get('rationale'), 120)}")
        if rec.get("key_risk"):
            lines.append(f"- ⚠️ 主要风险: {rec['key_risk']}")
        if rec.get("invalidation"):
            lines.append(f"- 失效条件: {rec['invalidation']}")
        lines.append("")

    return lines


def render_conclusion_card(result: dict[str, Any]) -> list[str]:
    """结论卡：主结论（分析→预测→动作）→ 推理链（宏观/板块 + 个股细化）→ 侧栏。"""
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
        "_阅读顺序：**A 主结论**（分析→预测→动作）→ **B 推理链**（宏观/板块骨架 + 个股①–④细化）→ "
        "**C 侧栏**（争议/尾部，须确认才升权）。下方 §0–§6 是完整论证。后果自负，仅供参考。_"
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
            "`watch_stocks`/必跟名单**不是**持仓。"
        )
        lines.append("")

    # ---------- A. 主结论 ----------
    lines.append("### A. 【主结论】分析：现在怎么看")
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

    lines.append("### A. 【主结论】预测：接下来怎么预期")
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

    lines.append("### A. 【主结论】动作：怎么做（④风控终局）")
    lines.append("")
    basis = (result.get("decision_summary") or {}).get("holdings_basis") or {}
    if basis.get("is_empty"):
        lines.append("_以下动作基于你声明的**真实持仓：空仓**（与模拟盘无关）；以④终局为准。_")
    elif basis.get("codes"):
        codes = "、".join(str(c) for c in basis["codes"][:8])
        lines.append(f"_以下动作基于你声明的**真实持仓**：{codes}（与模拟盘无关）；以④终局为准。_")
    else:
        lines.append("_以下为面向你声明持仓的操作建议（④终局）；模拟盘见后文独立章节。_")
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
    lines.append("### B. 推理链（宏观→板块 → 个股细化）")
    lines.append("")
    lines.append(
        "_B1 是整体骨架（情报→市场→配置→板块态度）；"
        "B2 是在此基础上对每只票的①研究→②草案→③辩论→④风控。"
        "①研究评级 ≠ 可开仓指令；可执行只看④与上方动作。_"
    )
    lines.append("")

    lines.append("#### B1. 宏观 → 板块")
    lines.append("")
    theme0 = ""
    themes = digest.get("headline_themes") or []
    if themes:
        theme0 = _one_line(themes[0], None)
    elif digest.get("market_narratives"):
        theme0 = _one_line(digest["market_narratives"][0], None)
    head = f"情报「{theme0 or '（见§0）'}」→ 市场「{phase} / {style}」(风险{risk})"
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

    lines.append("#### B2. 个股细化（①研究→②草案→③辩论→④风控）")
    lines.append("")
    stage_block = render_decision_stages_section(result)
    if stage_block:
        # 降级标题层级，并去掉外层「决策流程」二级标题（已由 B2 承接）
        for ln in stage_block:
            if ln.startswith("## "):
                continue
            if ln.startswith("### "):
                lines.append("##### " + ln[4:])
            else:
                lines.append(ln)
    else:
        lines.append("_本轮未写入 decision_stages（旧报告）；以 A 动作 / §4 终局为准。_")
        lines.append("")

    # ---------- C. 侧栏 ----------
    contested = _render_contested_block(
        result, heading="### C. 【侧栏】争议叙事 / 尾部情景"
    )
    if contested:
        lines.extend(contested)
    else:
        lines.append("### C. 【侧栏】争议叙事 / 尾部情景")
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
    lines.append("_以下为完整分析过程，供核对结论卡依据。_")
    lines.append("")

    if digest or agg:
        lines.append("## 0. 情报综述（新闻 / 政策 / 舆论）【主结论层】")
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
                    lines.append(f"- [{sig.get('direction', '?')}] {sig.get('signal', '')}（{sig.get('source', '')}）")
        if digest.get("risk_flags"):
            lines.append("")
            lines.append("**风险旗标**: " + "；".join(digest["risk_flags"]))
        radar_assess = digest.get("narrative_radar_assessment") or []
        if radar_assess:
            lines.append("")
            lines.append("**叙事雷达评估**:")
            for item in radar_assess[:4]:
                if isinstance(item, dict):
                    lines.append(
                        f"- [{item.get('stance', '?')}] {item.get('title') or item.get('track_id')} "
                        f"· {item.get('source_type', '-')} — {_one_line(item.get('why'), 80)}"
                    )
        lines.append("")

    radar = (result.get("intelligence") or {}).get("narrative_radar") or {}
    if radar:
        lines.append("## 0.1 叙事雷达（争议线索扫描）【侧栏】")
        lines.append("")
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
                f"- ● **{t.get('title')}** [{strength}] · {t.get('source_type', '-')} · 命中 {t.get('hit_count', 0)}"
            )
            for snip in (t.get("evidence_snippets") or [])[:2]:
                lines.append(f"  - {_one_line(snip, 100)}")
        lines.append("")

    market = result.get("market", {}).get("analysis") or {}
    lines.append("## 1. 市场阶段（数据 + 舆情综合）【主结论层】")
    lines.append("")
    lines.append(
        f"- **阶段**: {market.get('phase_label') or market.get('phase', 'unknown')} | "
        f"**风格**: {market.get('style_label') or market.get('style', 'unknown')}"
    )
    lines.append(f"- **风险**: {market.get('risk_level', 'unknown')} | **置信度**: {market.get('confidence', '-')}")
    if market.get("primary_driver"):
        lines.append(f"- **主驱动**: {market['primary_driver']}")
    lines.append(f"- {market.get('summary', '')}")

    policy = market.get("policy_assessment") or {}
    if policy:
        lines.append(f"- 政策基调: {policy.get('tone', '-')} | 板块配置倾向: {market.get('sector_allocation_hint', '-')}")

    sentiment = market.get("sentiment_assessment") or {}
    if sentiment:
        lines.append(
            f"- 舆情: {sentiment.get('level', '-')} | 叙事: {sentiment.get('narrative', '-')}"
        )
    if market.get("signals"):
        lines.append("- 信号: " + "；".join(market["signals"]))
    if market.get("contradictions"):
        lines.append("- ⚠️ 矛盾: " + "；".join(market["contradictions"]))
    vs = market.get("vs_prior") or {}
    if vs:
        lines.append(
            f"- 跨日: {vs.get('continuity', '-')} | 变化: {'; '.join(vs.get('what_changed') or [])[:120]}"
        )

    gl = ((result.get("intelligence") or {}).get("macro_raw") or {}).get("global_liquidity") or {}
    if gl:
        lines.append("")
        lines.append("### 1.0 全球流动性硬指标【主结论层】")
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
            lines.append(f"- LLM: {_one_line(liq.get('global_liquidity_note'), 120)}")
        lines.append("")

    micro = result.get("market_microstructure") or market.get("market_microstructure") or {}
    if micro:
        lines.append("")
        lines.append("### 1.1 微观结构 / 流动性断点【机制层】")
        lines.append("")
        lines.append(
            f"> {micro.get('plain_note') or ''} "
            "（机制信号可进入主结论纪律，但仍与侧栏「叙事指控」区分。）"
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
            lines.append(f"- LLM: {_one_line(market.get('microstructure_note'), 120)}")
        lines.append("")

    lines.extend(_render_contested_block(result, heading="### 【侧栏】争议叙事 / 尾部情景"))
    pol_scen = market.get("policy_market_scenario") or {}
    if pol_scen:
        lines.append("")
        lines.append("### 【侧栏】政策市假说（护盘 / 出清）")
        lines.append("")
        lines.append(
            f"- **状态**: `{pol_scen.get('status', '-')}` · 来源 {pol_scen.get('source_type', '-')}"
        )
        if pol_scen.get("thesis"):
            lines.append(f"- **假说**: {_one_line(pol_scen.get('thesis'), 160)}")
        if pol_scen.get("confirm_signals"):
            lines.append(
                "- **确认信号**: "
                + "；".join(_one_line(x, 50) for x in pol_scen["confirm_signals"][:3])
            )
        if pol_scen.get("falsify_signals"):
            lines.append(
                "- **证伪信号**: "
                + "；".join(_one_line(x, 50) for x in pol_scen["falsify_signals"][:3])
            )
        if pol_scen.get("implication"):
            lines.append(f"- **若成立**: {_one_line(pol_scen.get('implication'), 120)}")
        if pol_scen.get("evidence_now"):
            lines.append(
                "- **本轮线索**: "
                + "；".join(_one_line(x, 50) for x in pol_scen["evidence_now"][:3])
            )
        if pol_scen.get("note"):
            lines.append(f"- _{pol_scen['note']}_")
        lines.append("")
    snap = (result.get("market") or {}).get("snapshot") or {}
    if snap.get("style_proxy") or snap.get("limit_up_count") is not None:
        sp = snap.get("style_proxy") or {}
        lines.append(
            f"- 风格代理: cyb-hs300={sp.get('cyb_vs_hs300_1d', '-')} | "
            f"涨停={snap.get('limit_up_count', '-')} 跌停={snap.get('limit_down_count', '-')}"
        )
    lines.append("")

    lines.append("## 2. 板块筛选")
    lines.append("")
    su = result.get("sector_universe") or {}
    if su.get("note"):
        lines.append(f"> {su['note']}")
        lines.append("")
    if su.get("auto_sectors"):
        lines.append(
            f"- 关注板块: {'、'.join(su.get('watch_sectors') or []) or '—'}"
            f" · 资金流自动扩: {'、'.join(su['auto_sectors'])}"
        )
        lines.append("")
    by_sec = _recs_by_sector(result)
    for sec in result.get("sectors") or []:
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
            lines.append(f"  - 量化舆情: {quant.get('quant_score_100')}/100 ({quant.get('quant_label', '-')})")
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
    lines.append("")

    screen = result.get("screen") or {}
    if screen:
        lines.append("## 2.1 个股遴选漏斗")
        lines.append("")
        lines.append(
            f"> {screen.get('plain_note') or screen.get('note') or '量化遴选后进入深度分析。'}"
        )
        lines.append("")
        lines.append(
            "- **术语**: 必跟名单=`watch_stocks`+声明持仓（强制进深度池，**不占** `max_deep`）；"
            "量化池=打分入围；深度池=本轮 LLM 细读名单。必跟≠持仓。"
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
        must = screen.get("must_codes") or []
        if must:
            lines.append(f"- **必跟**: {'、'.join(str(c) for c in must)}")
        else:
            lines.append("- **必跟**: （空）深度池全部来自量化遴选")
        tops = screen.get("top_candidates") or []
        if tops:
            lines.append("- **量化前列**（进入深度池的优先候选）:")
            for t in tops[:10]:
                flag = " ·必跟" if t.get("must") else ""
                lines.append(
                    f"  - `{t.get('code')}` {t.get('name') or ''} "
                    f"分={t.get('screen_score')} PE={t.get('pe')} PB={t.get('pb')}{flag}"
                )
        if screen.get("degraded") or not screen.get("ok", True):
            lines.append(f"- ⚠️ **遴选降级**: {screen.get('note')}")
        lines.append("")

    lines.extend(render_stock_decision_chains(result))
    lines.extend(render_recommendations_section(result))

    lines.append("## 5. 复盘与经验")
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
        lines.append("### 维度复盘（全漏斗）")
        lines.append("")
        by_dim: dict[str, list] = {}
        for dr in dim_reviews:
            by_dim.setdefault(str(dr.get("dimension") or "other"), []).append(dr)
        for key in ("market", "sector", "narrative", "linkage"):
            group = by_dim.pop(key, [])
            if not group:
                continue
            lines.append(f"#### {dim_labels.get(key, key)}")
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
            lines.append(f"#### {key}")
            lines.append("")
            for dr in group:
                lines.append(
                    f"- **{dr.get('subject') or '?'}** → `{dr.get('outcome') or 'pending'}`"
                )
                if dr.get("diagnosis"):
                    lines.append(f"  - {dr['diagnosis']}")
            lines.append("")

    reviews = result.get("reviews") or []
    lines.append("### 个股动作复盘（thesis / 失效 / 纪律 / 轨迹）")
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
                lines.append(f"  - 失效触发: {', '.join(str(x) for x in (inv.get('fired') or [])[:3])}")
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
        lines.append("### 经验库")
        lines.append("")
        for item in patterns:
            lines.append(f"- 🔁 [pattern] {item}")
        for item in meta:
            lines.append(f"- 🆕 {item}")
        for item in sentiment_lessons:
            lines.append(f"- 📢 [舆情] {item}")
        seen = set()
        for item in lessons_used[:20]:
            content = (item.get('content') or '').strip()
            key = content[:80]
            if not content or key in seen:
                continue
            seen.add(key)
            lines.append(f"- 📚 [{item.get('category')}] {content}")
            if len(seen) >= 10:
                break
    lines.append("")

    trend = result.get("trend") or {}
    if trend:
        lines.append("## 6. 趋势更新（滚动）")
        lines.append("")
        regime = trend.get("market_regime") or {}
        lines.append(
            f"- **当前阶段**: {regime.get('current_label') or regime.get('current_phase', '-')} | "
            f"风格:{regime.get('current_style', '-')} | 风险:{regime.get('risk_level', '-')}"
        )
        if regime.get("regime_change"):
            lines.append(f"- **regime 变化**: {regime.get('regime_change')} — {regime.get('change_note', '')}")
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

    # 模拟账本放文末折叠，避免紧挨 §4 被当成真实持仓
    from money_more.sim.engine import render_sim_section

    lines.extend(render_sim_section(result.get("sim_portfolio"), result=result))

    lines.append("---")
    lines.append("*本报告由 AI 生成，仅供参考，不构成投资建议。*")

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
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = result.get("run_date", date.today().isoformat())
    md_path = reports_dir / f"{run_date}.md"
    json_path = reports_dir / f"{run_date}.json"

    md_path.write_text(render_daily_report(result), encoding="utf-8")
    json_path.write_text(dumps_json(result, indent=2), encoding="utf-8")

    digest = result.get("decision_digest")
    if digest:
        dig_dir = reports_dir / "digests"
        dig_dir.mkdir(parents=True, exist_ok=True)
        (dig_dir / f"{run_date}.json").write_text(dumps_json(digest, indent=2), encoding="utf-8")

    trend = result.get("trend") or {}
    if trend:
        save_trend_report(trend, reports_dir)

    return md_path


def save_trend_report(trend: dict[str, Any], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / "trend.md"
    json_path = reports_dir / "trend.json"
    md_path.write_text(render_trend_report(trend), encoding="utf-8")
    json_path.write_text(dumps_json(trend, indent=2), encoding="utf-8")
    return md_path