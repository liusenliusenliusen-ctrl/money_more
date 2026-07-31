"""决策分阶段轨迹：研究 → 组合草案 → 辩论 → 风控终局；终局后再写 portfolio_summary。"""

from __future__ import annotations

import copy
from typing import Any

from money_more.data.fetcher import normalize_code

_ACTION_CN = {
    "buy": "买入",
    "add": "加仓",
    "sell": "卖出",
    "hold": "持有",
    "watch": "观察",
}


def _one_line(text: Any, limit: int = 72) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def slim_recommendation(rec: dict[str, Any]) -> dict[str, Any]:
    """可序列化的建议快照（避免把整份辩论/因子卡塞进轨迹）。"""
    code = normalize_code(str(rec.get("code") or ""))
    debate = rec.get("debate") if isinstance(rec.get("debate"), dict) else {}
    out: dict[str, Any] = {
        "code": code,
        "action": str(rec.get("action") or "watch").lower(),
        "position_pct": rec.get("position_pct"),
        "confidence": rec.get("confidence"),
        "debate_status": rec.get("debate_status"),
        "referee": debate.get("referee") if debate else None,
        "decision_hint": debate.get("decision_hint") if debate else None,
        "rationale": _one_line(rec.get("rationale"), 100),
    }
    if rec.get("selection"):
        out["selection"] = str(rec.get("selection"))
    return out


def snapshot_recommendations(recs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [slim_recommendation(r) for r in (recs or []) if r]


def build_research_stage(stock_analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in stock_analyses or []:
        a = s.get("analysis") or {}
        code = normalize_code(str(s.get("code") or a.get("code") or ""))
        if not code:
            continue
        sc = s.get("factor_scorecard") or {}
        rows.append(
            {
                "code": code,
                "name": a.get("name") or "",
                "research_rating": str(a.get("research_rating") or "-").lower(),
                "confidence": a.get("confidence"),
                "factor_score": sc.get("total_score"),
                "summary": _one_line(a.get("summary"), 90),
            }
        )
    rows.sort(key=lambda r: (-float(r.get("factor_score") or 0), r["code"]))
    return rows


def _buy_add_codes(recs: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for r in recs or []:
        if str(r.get("action") or "").lower() not in ("buy", "add"):
            continue
        try:
            if r.get("position_pct") is not None and float(r.get("position_pct")) <= 0:
                continue
        except (TypeError, ValueError):
            pass
        code = normalize_code(str(r.get("code") or ""))
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def build_synthesis_audit(
    *,
    multi_agent_drafts: dict[str, Any] | None,
    portfolio_draft: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """对照主/副独立草案与综合后的②：保留/否决哪些买入（仅审计，不改建议）。"""
    drafts = multi_agent_drafts or {}
    if not drafts:
        return None
    meta = meta or {}
    primary = str(meta.get("primary") or "")
    secondary = str(meta.get("secondary") or "")
    agent_names = [n for n in (primary, secondary) if n] or list(drafts.keys())

    by_agent: dict[str, list[str]] = {}
    for name in agent_names:
        blob = drafts.get(name) if isinstance(drafts.get(name), dict) else None
        if not blob:
            continue
        by_agent[name] = _buy_add_codes(blob.get("recommendations"))

    if not by_agent:
        return None

    synth_buys = _buy_add_codes(portfolio_draft)
    synth_set = set(synth_buys)
    union: set[str] = set()
    for codes in by_agent.values():
        union |= set(codes)

    kept_u: list[str] = []
    seen_k: set[str] = set()
    for c in synth_buys:
        if c not in seen_k:
            seen_k.add(c)
            kept_u.append(c)

    dropped = sorted(union - synth_set)
    agent_only: dict[str, list[str]] = {}
    names = list(by_agent.keys())
    if len(names) >= 2:
        a, b = names[0], names[1]
        agent_only[a] = sorted(set(by_agent[a]) - set(by_agent[b]))
        agent_only[b] = sorted(set(by_agent[b]) - set(by_agent[a]))
        agreed = sorted(set(by_agent[a]) & set(by_agent[b]))
    else:
        agreed = sorted(union)

    return {
        "agents": {k: list(v) for k, v in by_agent.items()},
        "agent_buy_counts": {k: len(v) for k, v in by_agent.items()},
        "agreed_buys": agreed,
        "agent_only_buys": agent_only,
        "synthesized_buys": kept_u,
        "dropped_buys": dropped,
        "note": (
            "仅审计组合层取舍；① research buy 不计入。"
            "dropped_buys=至少一名分析师建议买入但综合未写入②。"
        ),
    }


def complete_stage_coverage(
    research: list[dict[str, Any]],
    portfolio_draft: list[dict[str, Any]],
    after_debate: list[dict[str, Any]],
    after_risk: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """深度池研究有、但未进入综合建议的代码：在②③④轨迹补「未入选」观察行。

    只改 decision_stages 快照，不改 recommendations / A3 / 模拟盘输入。
    """
    draft = list(portfolio_draft or [])
    debated = list(after_debate or [])
    risked = list(after_risk or [])
    have = {str(r.get("code") or "") for r in draft if r.get("code")}
    for row in draft:
        row.setdefault("selection", "selected")
    for row in debated:
        row.setdefault("selection", "selected")
    for row in risked:
        row.setdefault("selection", "selected")

    for r in research or []:
        code = str(r.get("code") or "")
        if not code or code in have:
            continue
        have.add(code)
        pad = {
            "code": code,
            "action": "watch",
            "position_pct": 0,
            "confidence": None,
            "debate_status": "n/a",
            "referee": None,
            "decision_hint": None,
            "selection": "not_selected",
            "rationale": "综合未纳入组合草案（①研究另见；≠漏跑）",
        }
        draft.append(dict(pad))
        debated.append(dict(pad))
        risked.append(dict(pad))

    def _sort_key(row: dict[str, Any]) -> tuple[int, str]:
        sel = 0 if row.get("selection") == "selected" else 1
        return (sel, str(row.get("code") or ""))

    draft.sort(key=_sort_key)
    debated.sort(key=_sort_key)
    risked.sort(key=_sort_key)
    return draft, debated, risked


def build_decision_stages(
    *,
    research: list[dict[str, Any]],
    portfolio_draft: list[dict[str, Any]],
    after_debate: list[dict[str, Any]],
    after_risk: list[dict[str, Any]],
    overrides: list[str] | None = None,
    draft_portfolio_summary: str | None = None,
    synthesis_audit: dict[str, Any] | None = None,
    complete_coverage: bool = True,
) -> dict[str, Any]:
    draft = list(portfolio_draft or [])
    debated = list(after_debate or [])
    risked = list(after_risk or [])
    if complete_coverage:
        draft, debated, risked = complete_stage_coverage(
            research, draft, debated, risked
        )
    out: dict[str, Any] = {
        "flow": [
            "① 个股研究（逐票 research_rating，≠开仓）",
            "② 组合草案（双分析师独立草案 → 综合委员合并）",
            "③ 多空辩论（仅对②中 buy/add）",
            "④ 风控终局（硬约束后的可执行动作）",
        ],
        "research": research,
        "portfolio_draft": draft,
        "after_debate": debated,
        "after_risk": risked,
        "overrides": list(overrides or [])[:40],
        "draft_portfolio_summary": draft_portfolio_summary or "",
        "plain_note": (
            "①研究评级≠开仓；②才是组合层取舍（含综合）；"
            "只有④里已入选且 buy/add（仓位>0）可执行并进模拟盘。"
            "「观察·未入选」=综合未写入组合（有意搁置，不是漏跑）。"
        ),
    }
    if synthesis_audit:
        out["synthesis_audit"] = synthesis_audit
    return out


def _forbid_reason(overrides: list[str]) -> str:
    text = "；".join(overrides)
    if "liquidity_stress" in text or "微观结构liquidity_stress" in text:
        return "微观结构 liquidity_stress：抑制/禁止新开仓"
    if "数据质量过低" in text or "数据降级" in text or "data_quality" in text:
        return "数据质量降级：禁止新开仓"
    if "crowded_sync" in text:
        return "微观结构拥挤同步：收紧总仓"
    if "global_liquidity=tightening" in text:
        return "全球流动性收紧：降低风险偏好"
    if "equity_bond=" in text or "ERP=" in text:
        return "股债相对价值偏贵/中性：压低总仓上限"
    if "现金流质量闸" in text:
        return "经营现金流质量不足：禁止新买"
    if "空仓禁止" in text:
        return "声明持仓为空：hold/sell/add 不可用"
    return ""


def build_final_portfolio_summary(
    recommendations: list[dict[str, Any]],
    *,
    holdings_basis: dict[str, Any] | None = None,
    overrides: list[str] | None = None,
    microstructure: dict[str, Any] | None = None,
    data_quality: dict[str, Any] | None = None,
) -> str:
    """在辩论+风控全部结束后生成摘要（不以 LLM 草案为准）。"""
    overrides = list(overrides or [])
    basis = holdings_basis or {}
    micro = microstructure or {}
    dq = data_quality or {}

    deploy: list[dict[str, Any]] = []
    watch_n = 0
    sell_n = 0
    hold_n = 0
    for rec in recommendations or []:
        action = str(rec.get("action") or "watch").lower()
        try:
            pct = float(rec.get("position_pct") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        if action in ("buy", "add") and pct > 0:
            deploy.append(rec)
        elif action == "sell":
            sell_n += 1
        elif action == "hold":
            hold_n += 1
        else:
            watch_n += 1

    parts: list[str] = []
    if basis.get("is_empty"):
        parts.append("持仓基准：声明真实持仓为空（空仓）。")
    elif basis.get("codes"):
        codes = "、".join(str(c) for c in basis["codes"][:10])
        parts.append(f"持仓基准：声明真实持仓 {codes}。")
    else:
        parts.append("持仓基准：以声明真实持仓为准。")

    micro_regime = str(micro.get("regime") or "normal")
    if micro_regime and micro_regime != "normal":
        parts.append(f"微观结构：`{micro_regime}`。")
    if dq.get("degraded"):
        parts.append(f"数据质量：DEGRADED（{dq.get('note') or '已收紧开仓'}）。")

    if deploy:
        bits = []
        for r in deploy[:8]:
            code = normalize_code(str(r.get("code") or ""))
            act = _ACTION_CN.get(str(r.get("action")), r.get("action"))
            try:
                pct = float(r.get("position_pct") or 0)
            except (TypeError, ValueError):
                pct = 0.0
            bits.append(f"{code}{act}{pct:.0f}%")
        total = 0.0
        for r in deploy:
            try:
                total += float(r.get("position_pct") or 0)
            except (TypeError, ValueError):
                pass
        parts.append(
            f"终局可执行开仓/加仓 {len(deploy)} 笔（合计约 {total:.0f}%）："
            + "、".join(bits)
            + ("…" if len(deploy) > 8 else "")
            + "。"
        )
        if watch_n:
            parts.append(f"另有 {watch_n} 只终局为观察。")
    else:
        reason = _forbid_reason(overrides)
        parts.append("终局**无可执行新开仓**（buy/add 且仓位>0 为空）。")
        if reason:
            parts.append(f"主因：{reason}。")
        else:
            parts.append("主因：组合草案经辩论/风控后未保留可开仓动作。")
        parts.append(
            f"计数：观察 {watch_n} · 持有 {hold_n} · 卖出 {sell_n}。"
            "模拟盘因此保持空仓或仅按既有模拟持仓调仓；研究层 buy 评级不构成开仓指令。"
        )

    # 关键覆写摘录
    key_ov = [
        o
        for o in overrides
        if any(
            k in str(o)
            for k in (
                "禁止新买",
                "liquidity_stress",
                "data_quality",
                "辩论裁判",
                "硬门禁",
                "空仓禁止",
            )
        )
    ][:4]
    if key_ov:
        parts.append("关键覆写：" + "；".join(key_ov) + "。")

    return "".join(parts)


def deep_copy_recs(recs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return copy.deepcopy(list(recs or []))
