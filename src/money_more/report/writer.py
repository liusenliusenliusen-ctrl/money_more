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


def _one_line(text: Any, limit: int = 72) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
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


def render_conclusion_card(result: dict[str, Any]) -> list[str]:
    """结论卡：从已有结果派生，便于外行速读验证。"""
    lines: list[str] = []
    market = (result.get("market") or {}).get("analysis") or {}
    digest = (result.get("intelligence") or {}).get("digest") or {}
    summary = result.get("decision_summary") or {}
    names = _stock_name_map(result)
    by_sec = _recs_by_sector(result)

    phase = market.get("phase_label") or market.get("phase") or "-"
    style = market.get("style_label") or market.get("style") or "-"
    risk = market.get("risk_level") or "-"
    conf = market.get("confidence", "-")
    driver = market.get("primary_driver") or "-"
    alloc = market.get("sector_allocation_hint") or "-"

    lines.append("## 结论卡（速读）")
    lines.append("")
    lines.append("_只看结论时读本节即可；下方 §0–§6 是完整论证。后果自负，仅供参考。_")
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

    lines.append("### 分析：现在怎么看")
    lines.append("")
    lines.append(f"- **环境**: {phase} · 风格 {style} · 风险 {risk} · 置信度 {conf}")
    if driver and driver != "-":
        lines.append(f"- **主驱动**: {_one_line(driver, 100)}")
    lines.append(f"- **配置倾向**: {alloc}")
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

    lines.append("### 预测：接下来怎么预期")
    lines.append("")
    outlook = summary.get("market_context") or market.get("summary") or ""
    if outlook:
        lines.append(f"- **主情景**: {_one_line(outlook, 140)}")
    inv = list(market.get("invalidation") or [])[:2]
    if not inv:
        # 从建议里抽失效条件
        for rec in (result.get("recommendations") or [])[:2]:
            if rec.get("invalidation"):
                inv.append(str(rec["invalidation"]))
            if len(inv) >= 2:
                break
    risks = list(digest.get("risk_flags") or [])[:2]
    if risks:
        lines.append(f"- **主要风险**: {'；'.join(_one_line(r, 60) for r in risks)}")
    if inv:
        lines.append(f"- **若出现则认错**: {'；'.join(_one_line(x, 60) for x in inv)}")
    vs = market.get("vs_prior") or {}
    if vs.get("continuity"):
        lines.append(
            f"- **相对上周**: {vs.get('continuity')}"
            + (f" — {_one_line('；'.join(vs.get('what_changed') or []), 80)}" if vs.get("what_changed") else "")
        )
    lines.append("")

    lines.append("### 动作：怎么做")
    lines.append("")
    basis = (result.get("decision_summary") or {}).get("holdings_basis") or {}
    if basis.get("is_empty"):
        lines.append("_以下动作基于你声明的**真实持仓：空仓**（与模拟盘无关）。_")
    elif basis.get("codes"):
        codes = "、".join(str(c) for c in basis["codes"][:8])
        lines.append(f"_以下动作基于你声明的**真实持仓**：{codes}（与模拟盘无关）。_")
    else:
        lines.append("_以下为面向你声明持仓的操作建议；模拟盘见后文独立章节。_")
    lines.append("")
    recs = result.get("recommendations") or []
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
            why = _one_line(rec.get("rationale"), 64)
            sector = rec.get("sector_tag") or infer_sector(code) or ""
            sec_s = f" · 板块:{sector}" if sector else ""
            lines.append(
                f"- **{label}** {code}{(' ' + name) if name else ''} "
                f"(置信度 {conf_s}{pos_s}{sec_s}) — {why}"
            )
    lines.append("")

    lines.append("### 板块：赛道态度")
    lines.append("")
    sectors = result.get("sectors") or []
    if not sectors:
        lines.append("- （无板块筛选）")
    else:
        for sec in sectors:
            a = sec.get("analysis") or {}
            name = str(a.get("sector") or sec.get("sector") or "")
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

    lines.append("### 逻辑链：维度如何串起来")
    lines.append("")
    lines.append(
        "_情报主题 → 市场阶段/风格 → 板块态度 → 个股研究 → 组合动作。"
        "下面每条是本轮可核对的因果链（不是另起一套结论）。_"
    )
    lines.append("")
    theme0 = ""
    themes = (digest.get("headline_themes") or [])
    if themes:
        theme0 = _one_line(themes[0], 40)
    elif digest.get("market_narratives"):
        theme0 = _one_line(digest["market_narratives"][0], 40)
    head = f"情报「{theme0 or '（见§0）'}」→ 市场「{phase} / {style}」(风险{risk})"
    lines.append(f"1. {head} → 配置倾向「{alloc}」")
    # 每条有对应个股动作的板块
    chain_i = 2
    for sec in sectors:
        a = sec.get("analysis") or {}
        name = str(a.get("sector") or sec.get("sector") or "")
        related = by_sec.get(name) or []
        if not related and str(a.get("priority") or "") != "high":
            continue
        stance = _sector_stance(a, related)
        if related:
            for r in related:
                code = str(r.get("code") or "")
                nm = names.get(code, "")
                act = _ACTION_LABEL.get(str(r.get("action")), r.get("action"))
                rating = ""
                for st in result.get("stocks") or []:
                    sa = st.get("analysis") or {}
                    if str(sa.get("code") or st.get("code")) == code:
                        rating = str(sa.get("research_rating") or "")
                        break
                rate_s = f"研究评级:{rating} → " if rating else ""
                lines.append(
                    f"{chain_i}. 板块「{name}」[{a.get('priority','-')}/"
                    f"{a.get('prosperity','-')}/{a.get('valuation','-')}] "
                    f"— {stance} → {rate_s}**{act}** {code}{(' '+nm) if nm else ''}"
                )
                chain_i += 1
        else:
            lines.append(
                f"{chain_i}. 板块「{name}」[{a.get('priority','-')}] — {stance} "
                f"→ 深度池暂无对应个股动作（板块结论仍约束追高/回避）"
            )
            chain_i += 1
        if chain_i > 6:
            break
    # 无板块映射的建议也列一行
    for rec in recs:
        code = str(rec.get("code") or "")
        tag = str(rec.get("sector_tag") or infer_sector(code) or "")
        if tag and tag in {str((s.get("analysis") or {}).get("sector") or s.get("sector") or "") for s in sectors}:
            continue
        act = _ACTION_LABEL.get(str(rec.get("action")), rec.get("action"))
        lines.append(
            f"{chain_i}. （未映射板块）→ **{act}** {code}{(' '+names.get(code,'')) if names.get(code) else ''}"
        )
        chain_i += 1
        if chain_i > 7:
            break
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

    dq = result.get("data_quality") or {}
    if dq:
        flag = "⚠️ DEGRADED" if dq.get("degraded") else "OK"
        lines.append(f"**数据质量**: {dq.get('score', '-')} ({flag}) — {dq.get('note', '')}")
        if dq.get("missing"):
            lines.append(f"- 缺失项: {', '.join(dq['missing'])}")
        if dq.get("screen_note"):
            lines.append(f"- 遴选: {dq['screen_note']}")
        lines.append("")

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
        lines.append("## 0. 情报综述（新闻 / 政策 / 舆论）")
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
        lines.append("")

    market = result.get("market", {}).get("analysis") or {}
    lines.append("## 1. 市场阶段（数据 + 舆情综合）")
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

    lines.append("## 3. 个股研究")
    lines.append("")
    for st in result.get("stocks") or []:
        a = st.get("analysis") or {}
        sent = _fmt_sentiment(a.get("sentiment"))
        quant = a.get("sentiment") or {}
        lines.append(
            f"- **{a.get('code', st.get('code'))}** {a.get('name', '')} | "
            f"评级:{a.get('research_rating', '-')} | "
            f"质量:{a.get('quality')} 估值:{a.get('valuation')}"
        )
        if quant.get("quant_score_100") is not None:
            lines.append(f"  - 量化舆情: {quant.get('quant_score_100')}/100 ({quant.get('quant_label', '-')})")
        if a.get("investment_thesis"):
            lines.append(f"  - 逻辑: {a['investment_thesis']}")
        if sent:
            lines.append(f"  - 舆情: {sent}")
        if a.get("expectation_gap"):
            lines.append(f"  - 预期差: {a['expectation_gap']}")
        sc = st.get("factor_scorecard") or a.get("factor_scorecard") or {}
        if sc.get("total_score") is not None:
            scores = sc.get("scores") or {}
            parts = " · ".join(f"{k}={v}" for k, v in scores.items())
            lines.append(
                f"  - 因子分: **{sc.get('total_score')}** ({sc.get('signal')}) | {parts}"
            )
        lines.append(f"  - {a.get('summary', '')}")
    lines.append("")

    summary = result.get("decision_summary") or {}
    lines.append("## 4. 买卖建议")
    lines.append("")
    basis = summary.get("holdings_basis") or {}
    if basis.get("is_empty"):
        lines.append(
            "> **持仓基准**：你声明的真实持仓为空（空仓）。"
            "下列 buy/watch 是「若按本轮研究结论配置」的建议，**不是**模拟盘状态，也不是假设你已持有某票。"
        )
    elif basis.get("codes"):
        codes = "、".join(str(c) for c in basis["codes"])
        lines.append(
            f"> **持仓基准**：你声明的真实持仓 {codes}。"
            "hold/add/sell 针对上述持仓；buy/watch 针对尚未持有的标的。"
            "**与后文「模拟组合」无关**——模拟盘只用于评估「若完全按建议执行」的效果。"
        )
    else:
        lines.append(
            "> **持仓基准**：以你声明的真实持仓为准；与后文模拟组合分离。"
        )
    lines.append("")
    if summary.get("market_context"):
        lines.append(f"> {summary['market_context']}")
        lines.append("")
    if summary.get("sentiment_regime_note"):
        lines.append(f"**舆情环境**: {summary['sentiment_regime_note']}")
        lines.append("")
    if summary.get("portfolio_summary"):
        lines.append(summary["portfolio_summary"])
        lines.append("")
    overrides = result.get("validation_overrides") or summary.get("validation_overrides") or []
    if overrides:
        lines.append("**风控覆写**:")
        for o in overrides[:12]:
            lines.append(f"- {o}")
        lines.append("")

    action_emoji = {
        "buy": "🟢买入",
        "add": "🟢加仓",
        "sell": "🔴卖出",
        "hold": "🟡持有",
        "watch": "👀观察",
    }
    sector_analysis = {
        str((sec.get("analysis") or {}).get("sector") or sec.get("sector") or ""): (sec.get("analysis") or {})
        for sec in (result.get("sectors") or [])
    }
    for rec in result.get("recommendations") or []:
        action = str(rec.get("action", "watch"))
        label = action_emoji.get(action, action)
        code = str(rec.get("code") or "")
        lines.append(f"### {label} {code}")
        lines.append("")
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
            sa = sector_analysis.get(sector) or {}
            if sa:
                lines.append(
                    f"- **承接板块**: {sector} "
                    f"[优先级 {sa.get('priority', '-')}, 景气 {sa.get('prosperity', '-')}, "
                    f"估值 {sa.get('valuation', '-')}, "
                    f"拥挤 {(sa.get('sentiment') or {}).get('crowding_risk', '-')}] "
                    f"— {_sector_stance(sa, [rec])}"
                )
            else:
                lines.append(f"- **承接板块**: {sector}")
        sc = rec.get("factor_scorecard") or {}
        if sc.get("total_score") is not None:
            lines.append(f"- 因子总分: {sc.get('total_score')} ({sc.get('signal')})")
        debate = rec.get("debate") or (result.get("debates") or {}).get(code)
        if debate and not debate.get("error"):
            lines.append(
                f"- 辩论: {debate.get('referee')} | haircut={debate.get('confidence_haircut')} | "
                f"矛盾={debate.get('bull_case', '')[:40]} / 空={debate.get('bear_case', '')[:40]}"
            )
        elif rec.get("debate_status") == "undebated":
            lines.append("- 辩论: **未辩论**（未进入 Top-K 多空对抗，置信度宜更保守）")
        lines.append(f"- 理由: {rec.get('rationale', '')}")
        if rec.get("evidence_chain"):
            lines.append("- 证据链:")
            for ev in rec["evidence_chain"]:
                lines.append(f"  - {ev}")
        if rec.get("key_risk"):
            lines.append(f"- ⚠️ 主要风险: {rec['key_risk']}")
        if rec.get("invalidation"):
            lines.append(f"- 失效条件: {rec['invalidation']}")
        lines.append("")

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
        lines.append(f"**数据质量分**: {dq.get('score', '-')} · {dq.get('note', '')}")
        if dq.get("missing"):
            lines.append(f"缺失源: {', '.join(dq['missing'])}")
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

    lines.extend(render_sim_section(result.get("sim_portfolio")))

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