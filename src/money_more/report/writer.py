from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from money_more.utils.json_util import dumps_json


def _fmt_sentiment(sent: dict[str, Any] | None) -> str:
    if not sent:
        return ""
    parts = []
    for key in ("level", "overall", "news_tone", "crowding_risk", "research_consensus"):
        if sent.get(key):
            parts.append(f"{key}={sent[key]}")
    return " | ".join(parts)


def render_daily_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    run_date = result.get("run_date", date.today().isoformat())
    lines.append("# money_more 每日决策报告")
    lines.append("")
    lines.append(f"**日期**: {run_date}")
    lines.append("")

    dq = result.get("data_quality") or {}
    if dq:
        flag = "⚠️ DEGRADED" if dq.get("degraded") else "OK"
        lines.append(f"**数据质量**: {dq.get('score', '-')} ({flag}) — {dq.get('note', '')}")
        if dq.get("missing"):
            lines.append(f"- 缺失项: {', '.join(dq['missing'])}")
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

    digest = (result.get("intelligence") or {}).get("digest") or {}
    macro_intel = (result.get("intelligence") or {}).get("macro_raw") or {}
    sentiment_overview = macro_intel.get("sentiment_overview") or {}
    agg = sentiment_overview.get("aggregate") or {}

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
    for sec in result.get("sectors") or []:
        a = sec.get("analysis") or {}
        worth = "✅" if a.get("worth_research") else "⏸"
        sent = _fmt_sentiment(a.get("sentiment"))
        quant = a.get("sentiment") or {}
        lines.append(
            f"- {worth} **{a.get('sector', sec.get('sector'))}** "
            f"[{a.get('priority', '-')}优先级] | "
            f"政策:{a.get('policy_wind')} 景气:{a.get('prosperity')} 估值:{a.get('valuation')}"
        )
        if quant.get("quant_score_100") is not None:
            lines.append(f"  - 量化舆情: {quant.get('quant_score_100')}/100 ({quant.get('quant_label', '-')})")
        if sent:
            lines.append(f"  - 舆情: {sent}")
        lines.append(f"  - {a.get('summary', '')}")
        if a.get("narrative"):
            lines.append(f"  - 叙事: {a['narrative']}")
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
    for rec in result.get("recommendations") or []:
        action = str(rec.get("action", "watch"))
        label = action_emoji.get(action, action)
        lines.append(f"### {label} {rec.get('code')}")
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
        sc = rec.get("factor_scorecard") or {}
        if sc.get("total_score") is not None:
            lines.append(f"- 因子总分: {sc.get('total_score')} ({sc.get('signal')})")
        debate = rec.get("debate") or (result.get("debates") or {}).get(str(rec.get("code")))
        if debate and not debate.get("error"):
            lines.append(
                f"- 辩论: {debate.get('referee')} | haircut={debate.get('confidence_haircut')} | "
                f"矛盾={debate.get('bull_case', '')[:40]} / 空={debate.get('bear_case', '')[:40]}"
            )
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
    reviews = result.get("reviews") or []
    if not reviews:
        lines.append("_今日无新增复盘（或暂无可复盘的历史建议）_")
    else:
        for rv in reviews:
            cat = rv.get("diagnosis_category") or ""
            lines.append(
                f"- **{rv.get('stock_code')}** [{rv.get('outcome')}] "
                f"收益:{rv.get('return_pct', '-%')} | [{cat}] {rv.get('diagnosis', '')}"
            )
            if rv.get("lesson"):
                lines.append(f"  - 经验: {rv['lesson']}")
            if rv.get("prompt_adjustment"):
                lines.append(f"  - 分析改进: {rv['prompt_adjustment']}")
    lines.append("")

    meta = result.get("meta_lessons") or []
    sentiment_lessons = result.get("sentiment_lessons") or []
    lessons_used = result.get("lessons_used") or []
    if meta or sentiment_lessons or lessons_used:
        lines.append("### 经验库")
        lines.append("")
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