"""叙事雷达：从公开情报中扫描高争议/尾部叙事线索（规则层，供 LLM 侧栏使用）。

定位：侧栏情景，不是主剧本。区分 hard_data / market_pricing / web_narrative。
"""

from __future__ import annotations

from typing import Any

# 轨道：关键词命中 → 证据片段；不直接下买卖结论
_TRACKS: list[dict[str, Any]] = [
    {
        "id": "us_liquidity_debt",
        "title": "美债 / 美元流动性紧缩外溢",
        "default_source": "mixed",
        "keywords": (
            "美债",
            "美债收益率",
            "十年期美债",
            "美元指数",
            "DXY",
            "美联储",
            "降息",
            "加息",
            "流动性危机",
            "国债上限",
            "财政赤字",
            "美元流动性",
            "美债风暴",
        ),
    },
    {
        "id": "ai_valuation_bubble",
        "title": "AI / 科技估值泡沫与拥挤",
        "default_source": "web_narrative",
        "keywords": (
            "AI泡沫",
            "人工智能泡沫",
            "英伟达",
            "英伟达泡沫",
            "科技股泡沫",
            "估值泡沫",
            "拥挤交易",
            "算力泡沫",
            "大模型泡沫",
            "美股科技",
            "纳斯达克泡沫",
        ),
    },
    {
        "id": "quant_microstructure",
        "title": "量化拥挤 / 微观结构扰动",
        "default_source": "market_pricing",
        "keywords": (
            "量化",
            "量化交易",
            "高频",
            "程序化",
            "量化踩踏",
            "量化回撤",
            "同涨同跌",
            "流动性枯竭",
            "闪崩",
            "被动基金",
            "ETF 赎回",
            "ETF赎回",
        ),
    },
    {
        "id": "geopolitical_risk",
        "title": "地缘冲突 / 避险外溢",
        "default_source": "web_narrative",
        "keywords": (
            "地缘",
            "冲突",
            "战争",
            "袭击",
            "导弹",
            "军事打击",
            "中东",
            "伊朗",
            "以军",
            "俄乌",
            "避险",
            "避险情绪",
            "油价",
            "原油",
            "能源危机",
        ),
    },
    {
        "id": "policy_national_team",
        "title": "政策市 / 国家队护盘与退出",
        "default_source": "web_narrative",
        "keywords": (
            "国家队",
            "平准基金",
            "中央汇金",
            "汇金增持",
            "汇金减持",
            "救市",
            "护盘",
            "稳市",
            "中证金",
            "证金公司",
            "ETF 净买入",
            "ETF净买入",
            "宽基ETF",
            "政策底",
            "出清",
        ),
    },
]

_POLICY_TEMPLATE = {
    "id": "national_team_exit",
    "title": "护盘任务完成后资金出清（政策市假说）",
    "thesis": (
        "若前期稳市力量已完成阶段性任务，后续可能减少对宽基/权重的净支撑，"
        "表现为护盘痕迹减弱、关键 ETF 或权重股持续供给——属可跟踪假说，非已证实事实。"
    ),
    "entry_conditions": [
        "稳市相关主体（汇金/平准/国家队叙事）热度从高位回落，同时指数仍弱或分化加剧",
        "宽基 ETF 出现持续净赎回或「救市买入」线索明显减少",
        "金融/权重护盘股相对大盘转弱，且伴随成交额结构异常",
    ],
    "observe_metrics": [
        "北向/两融周度趋势是否与「护盘托底」叙事一致",
        "银行/保险/宽基 ETF 相关资金流与舆情关键词变化",
        "政策口径：稳增长/资本市场表态是加码还是边际沉默",
    ],
    "falsify_signals": [
        "权威口径明确加码稳市或可见的持续大规模净买入再现",
        "权重与宽基同步放量企稳且风险溢价回落",
    ],
    "if_true_portfolio_implication": "提高现金与防御权重，推迟左侧抄底；待确认信号消失后再恢复风险敞口",
}


def build_narrative_radar(
    macro_intel: dict[str, Any] | None,
    market_snapshot: dict[str, Any] | None = None,
    microstructure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """扫描宏观情报文本，产出叙事雷达 + 政策市假说模板。"""
    texts = _collect_texts(macro_intel or {})
    tracks: list[dict[str, Any]] = []
    for track in _TRACKS:
        hits = _scan_track(texts, track["keywords"])
        strength = _strength(len(hits))
        source = track["default_source"]
        if track["id"] == "us_liquidity_debt" and hits:
            source = "mixed"
        micro_hit = _quant_market_hint(market_snapshot, microstructure)
        if track["id"] == "quant_microstructure" and micro_hit:
            strength = _bump_strength(strength)
            source = "market_pricing"
            snip = micro_hit if isinstance(micro_hit, str) else "市场结构提示：波动/同向性异常"
            hits = hits or [{"snippet": snip, "source": "market_microstructure"}]
        gl = (macro_intel or {}).get("global_liquidity") or {}
        if track["id"] == "us_liquidity_debt" and gl.get("stance") in ("tightening", "easing"):
            strength = _bump_strength(strength)
            if gl.get("stance") == "tightening":
                strength = _bump_strength(strength)
            source = "hard_data"
            snip = str(gl.get("plain_note") or f"全球流动性={gl.get('stance')}")
            if not hits:
                hits = [{"snippet": snip, "source": "global_liquidity"}]
            elif snip not in (h.get("snippet") for h in hits):
                hits.insert(0, {"snippet": snip, "source": "global_liquidity"})
        tracks.append(
            {
                "id": track["id"],
                "title": track["title"],
                "source_type": source,
                "signal_strength": strength,
                "hit_count": len(hits),
                "evidence_snippets": [h["snippet"] for h in hits[:4]],
                "evidence": hits[:4],
            }
        )

    policy_hits = next((t for t in tracks if t["id"] == "policy_national_team"), None)
    status = "inactive"
    evidence_now: list[str] = []
    if policy_hits:
        evidence_now = list(policy_hits.get("evidence_snippets") or [])
        if policy_hits.get("signal_strength") == "high":
            status = "elevated"
        elif policy_hits.get("signal_strength") == "medium":
            status = "watch"
        elif policy_hits.get("signal_strength") == "low":
            status = "watch"

    policy = {
        **_POLICY_TEMPLATE,
        "status": status,
        "source_type": "web_narrative" if status != "inactive" else "template",
        "evidence_now": evidence_now,
        "counter_evidence": [],
        "note": "侧栏假说：无足够证据时不得升为主剧本或单独驱动买入。",
    }

    active = [t for t in tracks if t["signal_strength"] != "none"]
    return {
        "tracks": tracks,
        "active_track_ids": [t["id"] for t in active],
        "policy_market_hypothesis": policy,
        "plain_note": (
            f"叙事雷达命中 {len(active)}/{len(tracks)} 条轨道；"
            f"政策市假说状态={status}。以下为线索扫描，需经确认/证伪信号才可升权。"
        ),
    }


def seed_contested_from_radar(radar: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """规则层预填「争议叙事」卡片，供结论卡在 LLM 缺失时回退。"""
    out: list[dict[str, Any]] = []
    for t in radar.get("tracks") or []:
        if t.get("signal_strength") in ("none", None):
            continue
        out.append(
            {
                "title": t.get("title"),
                "track_id": t.get("id"),
                "source_type": t.get("source_type") or "web_narrative",
                "probability": _strength_to_prob(str(t.get("signal_strength"))),
                "confirm_signals": _default_confirm(str(t.get("id"))),
                "falsify_signals": _default_falsify(str(t.get("id"))),
                "portfolio_if_true": _default_implication(str(t.get("id"))),
                "evidence": (t.get("evidence_snippets") or [])[:2],
                "note": "规则雷达预填；以市场分析 LLM 修订版为准",
            }
        )
        if len(out) >= limit:
            break
    # 政策市假说始终可占一侧栏位（即便 inactive 也简要列出）
    pol = radar.get("policy_market_hypothesis") or {}
    if pol and not any(x.get("track_id") == "policy_national_team" for x in out):
        if len(out) < limit and pol.get("status") != "inactive":
            out.append(
                {
                    "title": pol.get("title"),
                    "track_id": "policy_national_team",
                    "source_type": pol.get("source_type") or "web_narrative",
                    "probability": "medium" if pol.get("status") == "elevated" else "low",
                    "confirm_signals": list(pol.get("entry_conditions") or [])[:3],
                    "falsify_signals": list(pol.get("falsify_signals") or [])[:3],
                    "portfolio_if_true": pol.get("if_true_portfolio_implication"),
                    "evidence": list(pol.get("evidence_now") or [])[:2],
                    "note": "政策市假说模板",
                }
            )
    return out[:limit]


def merge_contested_narratives(
    llm_items: list[Any] | None,
    radar: dict[str, Any] | None,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """LLM 输出优先，不足时用雷达预填补齐。"""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in llm_items or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        key = title[:40]
        if key in seen:
            continue
        seen.add(key)
        merged.append(_normalize_contested(raw))
        if len(merged) >= limit:
            return merged
    for seeded in seed_contested_from_radar(radar or {}, limit=limit):
        key = str(seeded.get("title") or "")[:40]
        if key in seen:
            continue
        seen.add(key)
        merged.append(seeded)
        if len(merged) >= limit:
            break
    return merged


def merge_policy_market_scenario(
    llm_scen: dict[str, Any] | None,
    radar: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并 LLM 政策市情景与雷达模板。"""
    base = dict((radar or {}).get("policy_market_hypothesis") or _POLICY_TEMPLATE)
    llm = llm_scen if isinstance(llm_scen, dict) else {}
    out = {
        "id": llm.get("id") or base.get("id") or "national_team_exit",
        "title": llm.get("title") or base.get("title"),
        "status": llm.get("status") or base.get("status") or "inactive",
        "thesis": llm.get("thesis") or base.get("thesis"),
        "confirm_signals": list(llm.get("confirm_signals") or llm.get("entry_conditions") or base.get("entry_conditions") or [])[
            :4
        ],
        "falsify_signals": list(llm.get("falsify_signals") or base.get("falsify_signals") or [])[:4],
        "observe_metrics": list(llm.get("observe_metrics") or base.get("observe_metrics") or [])[:4],
        "implication": llm.get("implication")
        or llm.get("if_true_portfolio_implication")
        or base.get("if_true_portfolio_implication"),
        "evidence_now": list(llm.get("evidence_now") or base.get("evidence_now") or [])[:4],
        "source_type": llm.get("source_type") or base.get("source_type") or "template",
        "note": llm.get("note")
        or base.get("note")
        or "侧栏假说：不得单独作为买入依据。",
    }
    return out


def _normalize_contested(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(raw.get("title") or ""),
        "track_id": raw.get("track_id"),
        "source_type": str(raw.get("source_type") or "web_narrative"),
        "probability": str(raw.get("probability") or "low"),
        "confirm_signals": list(raw.get("confirm_signals") or [])[:4],
        "falsify_signals": list(raw.get("falsify_signals") or [])[:4],
        "portfolio_if_true": raw.get("portfolio_if_true") or raw.get("implication") or "",
        "evidence": list(raw.get("evidence") or [])[:3],
        "note": raw.get("note") or "侧栏情景，非主剧本",
    }


def _collect_texts(macro: dict[str, Any]) -> list[dict[str, str]]:
    buckets: list[tuple[str, list[Any]]] = [
        ("policy_news", list(macro.get("policy_news") or [])),
        ("global_news", list(macro.get("global_news") or [])),
        ("global_news_sina", list(macro.get("global_news_sina") or [])),
        ("rss_telegraph", list(macro.get("rss_telegraph") or [])),
        ("rss_important", list(macro.get("rss_important") or [])),
        ("tushare_macro_news", list(macro.get("tushare_macro_news") or [])),
    ]
    out: list[dict[str, str]] = []
    for source, items in buckets:
        for item in items[:12]:
            text = _item_text(item)
            if text:
                out.append({"source": source, "text": text})
    return out


def _item_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return str(item)[:200]
    parts = []
    for k in ("title", "内容", "content", "摘要", "summary", "新闻标题", "标题"):
        v = item.get(k)
        if v:
            parts.append(str(v))
    if not parts:
        parts.append(str(item)[:240])
    return " ".join(parts)


def _scan_track(texts: list[dict[str, str]], keywords: tuple[str, ...]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in texts:
        text = row["text"]
        for kw in keywords:
            if kw in text:
                snip = _snippet(text, kw)
                if snip in seen:
                    break
                seen.add(snip)
                hits.append({"snippet": snip, "source": row["source"], "keyword": kw})
                break
    return hits


def _snippet(text: str, kw: str, radius: int = 36) -> str:
    idx = text.find(kw)
    if idx < 0:
        return text[:80]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(kw) + radius)
    s = text[start:end].replace("\n", " ").strip()
    if start > 0:
        s = "…" + s
    if end < len(text):
        s = s + "…"
    return s[:120]


def _strength(n: int) -> str:
    if n >= 4:
        return "high"
    if n >= 2:
        return "medium"
    if n >= 1:
        return "low"
    return "none"


def _bump_strength(s: str) -> str:
    order = ["none", "low", "medium", "high"]
    i = order.index(s) if s in order else 0
    return order[min(i + 1, len(order) - 1)]


def _quant_market_hint(
    snap: dict[str, Any] | None,
    microstructure: dict[str, Any] | None = None,
) -> str | bool:
    """微观结构升权：优先用 assess_market_microstructure 结果。"""
    micro = microstructure or {}
    regime = str(micro.get("regime") or "")
    if regime in ("crowded_sync", "liquidity_stress"):
        return str(micro.get("plain_note") or f"微观结构={regime}")
    if micro.get("flags"):
        return "；".join(str(x) for x in micro["flags"][:2])
    if not snap:
        return False
    try:
        ld = snap.get("limit_down_count")
        if isinstance(ld, int) and ld >= 40:
            return f"跌停家数={ld}"
    except (TypeError, ValueError):
        pass
    return False


def _strength_to_prob(strength: str) -> str:
    return {"high": "medium", "medium": "low", "low": "low"}.get(strength, "low")


def _default_confirm(track_id: str) -> list[str]:
    return {
        "us_liquidity_debt": [
            "美债利率或美元流动性指标连续恶化并向风险资产传导",
            "全球风险资产同步大幅回撤且 A 股无法走出独立行情",
        ],
        "ai_valuation_bubble": [
            "海外科技巨头估值与盈利预期同步下修",
            "A 股算力/应用链拥挤交易出现持续净流出与估值杀",
        ],
        "quant_microstructure": [
            "出现显著同涨同跌、流动性真空或量化相关踩踏线索",
            "基本面分化加大但价格相关性反而上升",
        ],
        "policy_national_team": [
            "护盘主体净买入线索明显减弱，同时权重/宽基持续供给",
            "稳市政策口径边际转弱且指数失守关键支撑",
        ],
    }.get(track_id, ["出现可核对的强化证据"])


def _default_falsify(track_id: str) -> list[str]:
    return {
        "us_liquidity_debt": ["美债利率回落、美元流动性缓和，风险资产企稳"],
        "ai_valuation_bubble": ["龙头盈利兑现、估值消化后资金重新流入而非单边杀估值"],
        "quant_microstructure": ["个股分化恢复、流动性指标正常化"],
        "policy_national_team": ["可见的稳市加码或护盘净买入再现并伴随波动收敛"],
    }.get(track_id, ["出现明确反证"])


def _default_implication(track_id: str) -> str:
    return {
        "us_liquidity_debt": "降低总风险敞口，提高现金与高股息/防御权重",
        "ai_valuation_bubble": "回避最拥挤成长赛道追高，等待估值消化信号",
        "quant_microstructure": "降低对短线量价规律的依赖，放宽止损噪声、收紧新开仓",
        "policy_national_team": "推迟抄底，等待出清确认或新的稳市证据",
    }.get(track_id, "提高观望与现金比例，直至确认/证伪")
