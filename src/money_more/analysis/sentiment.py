"""财经新闻舆情打分：词典 + 规则 + 事件识别。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from money_more.data.fetcher import normalize_code


POSITIVE_WORDS: dict[str, float] = {
    "增长": 1.0, "大增": 1.4, "超预期": 1.5, "创新高": 1.3, "突破": 1.0, "涨停": 1.2,
    "利好": 1.2, "受益": 0.9, "获批": 1.3, "中标": 1.2, "回购": 1.1, "增持": 1.2, "加仓": 1.0,
    "分红": 0.8, "扩产": 0.9, "景气": 1.0, "回暖": 0.9, "复苏": 1.0, "扭亏": 1.3,
    "上调": 1.0, "买入": 0.8, "推荐": 0.7, "强推": 1.0, "目标价": 0.4, "降准": 1.2, "降息": 1.1,
    "刺激": 0.8, "扶持": 0.9, "补贴": 0.9, "合作": 0.5, "订单": 0.8, "签约": 0.7,
    "盈利": 0.9, "净利润": 0.6, "营收": 0.5, "放量": 0.6, "净流入": 0.9, "北向": 0.4,
    "国产替代": 1.1, "自主可控": 1.0, "高股息": 0.8, "分红提升": 1.0, "超配": 0.9,
    "业绩预增": 1.3, "扭亏为盈": 1.4, "回购注销": 1.1,
}

NEGATIVE_WORDS: dict[str, float] = {
    "下滑": 1.0, "下降": 0.9, "亏损": 1.3, "暴雷": 1.6, "退市": 1.8, "立案": 1.5, "调查": 1.2,
    "警示": 1.1, "处罚": 1.2, "违规": 1.1, "减持": 1.2, "抛售": 1.1, "质押": 0.8, "爆仓": 1.5,
    "低于预期": 1.3, "不及预期": 1.2, "下调": 1.0, "卖出": 1.0, "回避": 0.9, "跌停": 1.2,
    "利空": 1.2, "风险": 0.6, "制裁": 1.1, "关税": 0.9, "产能过剩": 1.0, "价格战": 0.9,
    "净流出": 0.9, "解禁": 0.8, "ST": 1.0, "暂停上市": 1.6, "诉讼": 1.0, "仲裁": 0.7,
    "延迟": 0.5, "取消": 0.7, "终止": 0.9, "失败": 1.0, "爆雷": 1.6,
    "业绩预减": 1.3, "商誉减值": 1.2, "应收账款": 0.5, "现金流紧张": 1.1, "债务违约": 1.5,
    "监管约谈": 1.2, "财务造假": 1.8, "退市风险": 1.7,
}

UNCERTAINTY_WORDS: set[str] = {"传闻", "或将", "可能", "疑似", "关注", "据说", "或", "有望", "预计"}

NEGATIONS: set[str] = {
    "不", "未", "没", "无", "非", "别", "莫", "勿", "难以", "并未", "未能", "没有",
    "不会", "不再", "尚未", "无法", "并非", "绝不",
}

INTENSIFIERS: dict[str, float] = {"大幅": 1.3, "显著": 1.2, "明显": 1.15, "持续": 1.1, "小幅": 0.7, "略有": 0.75}

EVENT_TAG_LABELS: dict[str, str] = {
    "macro_positive": "宏观宽松/降准降息",
    "macro_negative": "宏观收紧/加息",
    "holder_positive": "回购增持/股东利好",
    "holder_negative": "减持质押/股东利空",
    "business_positive": "订单中标/业务扩张",
    "regulatory_negative": "监管立案/处罚",
    "earnings_positive": "业绩预增/扭亏",
    "earnings_negative": "业绩预减/低于预期",
    "geopolitical_negative": "地缘冲突/战争风险",
    "trade_friction": "贸易摩擦/关税制裁",
    "risk_off": "避险/风险偏好下降",
    "energy_shock": "油价/能源冲击",
    "policy_support": "产业政策/扶持",
}

EVENT_PATTERNS: list[tuple[str, float, str]] = [
    (r"降准|降息|下调LPR", 1.2, "macro_positive"),
    (r"加息|加准|收紧", -1.0, "macro_negative"),
    (r"回购|增持|举牌", 1.0, "holder_positive"),
    (r"减持|清仓|质押爆仓", -1.2, "holder_negative"),
    (r"中标|签订|获订单", 0.9, "business_positive"),
    (r"立案|调查|处罚|警示", -1.3, "regulatory_negative"),
    (r"业绩预增|扭亏|超预期", 1.2, "earnings_positive"),
    (r"业绩预减|首亏|续亏|低于预期", -1.2, "earnings_negative"),
    (r"地缘|冲突|战争|袭击|导弹|军事打击|中东|伊朗|以军|俄乌", -1.3, "geopolitical_negative"),
    (r"关税|贸易摩擦|出口管制|加征关税|贸易制裁|实体清单", -1.2, "trade_friction"),
    (r"避险|风险偏好下降|恐慌情绪|VIX|黄金大涨|美债风暴", -1.0, "risk_off"),
    (r"油价|原油|能源危机|OPEC|页岩油", -0.8, "energy_shock"),
    (r"产业政策|扶持|补贴|国产替代|自主可控|专项规划", 0.9, "policy_support"),
]


@dataclass
class SentimentResult:
    score: float  # -1 ~ +1
    score_100: float  # 0 ~ 100
    label: str  # very_negative|negative|neutral|positive|very_positive
    confidence: float
    positive_hits: list[str] = field(default_factory=list)
    negative_hits: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    uncertainty: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "score_100": round(self.score_100, 2),
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "positive_hits": self.positive_hits,
            "negative_hits": self.negative_hits,
            "events": self.events,
            "uncertainty": self.uncertainty,
        }


class FinancialSentimentScorer:
    """A 股财经文本舆情打分器（词典 + 规则，无需额外模型服务）。"""

    def score_text(self, text: str) -> SentimentResult:
        text = re.sub(r"\s+", "", text or "")
        if not text:
            return SentimentResult(0.0, 50.0, "neutral", 0.0)

        pos_score = 0.0
        neg_score = 0.0
        pos_hits: list[str] = []
        neg_hits: list[str] = []
        events: list[str] = []

        for word, weight in POSITIVE_WORDS.items():
            for m in re.finditer(re.escape(word), text):
                w = weight * self._context_multiplier(text, m.start(), positive=True)
                pos_score += w
                pos_hits.append(word)

        for word, weight in NEGATIVE_WORDS.items():
            for m in re.finditer(re.escape(word), text):
                w = weight * self._context_multiplier(text, m.start(), positive=False)
                neg_score += w
                neg_hits.append(word)

        for pattern, impact, tag in EVENT_PATTERNS:
            if re.search(pattern, text):
                events.append(tag)
                if impact > 0:
                    pos_score += abs(impact)
                else:
                    neg_score += abs(impact)

        uncertainty = any(w in text for w in UNCERTAINTY_WORDS)
        raw = pos_score - neg_score
        if uncertainty:
            raw *= 0.65

        # tanh 归一化到 -1~1
        score = math.tanh(raw / 4.0)
        score_100 = (score + 1) / 2 * 100
        label = self._label(score)
        hit_count = len(set(pos_hits)) + len(set(neg_hits)) + len(events)
        confidence = min(1.0, 0.25 + hit_count * 0.08)
        if uncertainty:
            confidence *= 0.85

        return SentimentResult(
            score=score,
            score_100=score_100,
            label=label,
            confidence=confidence,
            positive_hits=sorted(set(pos_hits)),
            negative_hits=sorted(set(neg_hits)),
            events=events,
            uncertainty=uncertainty,
        )

    def score_news_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            return {
                "aggregate": {"score": 0.0, "score_100": 50.0, "label": "neutral", "count": 0},
                "items": [],
            }

        scored_items: list[dict[str, Any]] = []
        weighted_sum = 0.0
        weight_total = 0.0
        labels: list[str] = []

        for idx, item in enumerate(items):
            title = str(item.get("title") or item.get("新闻标题") or item.get("标题") or "")
            content = str(item.get("content") or item.get("新闻内容") or item.get("内容") or item.get("summary") or "")
            text = f"{title} {content}"
            sr = self.score_text(text)
            importance = 1.0
            if item.get("level") in ("A", "B"):
                importance = 1.4
            if item.get("category") in ("telegraph_red", "telegraph_important", "announcement"):
                importance *= 1.15

            weighted_sum += sr.score * sr.confidence * importance
            weight_total += sr.confidence * importance
            labels.append(sr.label)

            scored = dict(item)
            scored["sentiment"] = sr.to_dict()
            scored_items.append(scored)

        agg_score = weighted_sum / weight_total if weight_total else 0.0
        agg_100 = (agg_score + 1) / 2 * 100
        dist = self._distribution(labels)
        enrich = self._aggregate_enrichments(scored_items, agg_score, dist)
        return {
            "aggregate": {
                "score": round(agg_score, 4),
                "score_100": round(agg_100, 2),
                "label": self._label(agg_score),
                "count": len(scored_items),
                "distribution": dist,
                **enrich,
            },
            "items": scored_items,
        }

    def score_for_entity(
        self,
        all_items: list[dict[str, Any]],
        keywords: list[str],
        limit: int = 15,
    ) -> dict[str, Any]:
        matched = []
        for item in all_items:
            text = " ".join(
                str(item.get(k, ""))
                for k in ("title", "content", "新闻标题", "新闻内容", "标题", "内容", "summary")
            )
            if any(kw and kw in text for kw in keywords):
                matched.append(item)
        return self.score_news_items(matched[:limit])

    @staticmethod
    def _label(score: float) -> str:
        if score >= 0.45:
            return "very_positive"
        if score >= 0.15:
            return "positive"
        if score <= -0.45:
            return "very_negative"
        if score <= -0.15:
            return "negative"
        return "neutral"

    @staticmethod
    def _distribution(labels: list[str]) -> dict[str, int]:
        dist: dict[str, int] = {}
        for lb in labels:
            dist[lb] = dist.get(lb, 0) + 1
        return dist

    def _aggregate_enrichments(
        self,
        scored_items: list[dict[str, Any]],
        agg_score: float,
        dist: dict[str, int],
    ) -> dict[str, Any]:
        """汇总层：事件分布、极端情绪标签（供因子卡与 LLM 交叉验证）。"""
        count = len(scored_items)
        event_counts: dict[str, int] = {}
        for item in scored_items:
            for ev in (item.get("sentiment") or {}).get("events") or []:
                event_counts[str(ev)] = event_counts.get(str(ev), 0) + 1

        very_pos = dist.get("very_positive", 0)
        very_neg = dist.get("very_negative", 0)
        pos_ratio = (dist.get("positive", 0) + very_pos) / count if count else 0.0
        neg_ratio = (dist.get("negative", 0) + very_neg) / count if count else 0.0
        agg_100 = (agg_score + 1) / 2 * 100

        extreme: str | None = None
        if count >= 3:
            if (agg_100 >= 72 and very_pos >= max(2, count * 0.2)) or very_pos >= count * 0.35:
                extreme = "euphoria"
            elif (agg_100 <= 28 and very_neg >= max(2, count * 0.2)) or very_neg >= count * 0.35:
                extreme = "panic"

        top_events = sorted(event_counts.items(), key=lambda x: (-x[1], x[0]))[:6]
        return {
            "event_distribution": dict(top_events),
            "extreme": extreme,
            "positive_ratio": round(pos_ratio, 3),
            "negative_ratio": round(neg_ratio, 3),
        }

    @staticmethod
    def _context_multiplier(text: str, index: int, positive: bool) -> float:
        # 扩大否定窗口：覆盖「不会增长」「并未改善」等
        window = text[max(0, index - 8) : index]
        mult = 1.0
        for neg in NEGATIONS:
            if neg in window:
                mult *= -0.8
                break
        for intensifier, factor in INTENSIFIERS.items():
            if intensifier in window:
                mult *= factor
        return mult


def _sector_match_keywords(sector_name: str) -> list[str]:
    """板块名 + 行业别名，供宏观语料关键词匹配。"""
    keywords = [sector_name]
    from money_more.analysis.sector_map import _INDUSTRY_ALIASES

    for keys, label in _INDUSTRY_ALIASES:
        if label == sector_name:
            keywords.extend(keys)
    # 未配置别名的板块：用名称子串提高命中率（如「汽车服务及其他」→「汽车」）
    if len(keywords) == 1 and len(sector_name) >= 4:
        keywords.append(sector_name[:2])
        if len(sector_name) >= 6:
            keywords.append(sector_name[:4])
    return list(dict.fromkeys(k for k in keywords if k))


def build_industry_sentiment_index(
    news_pool: list[dict[str, Any]],
    sector_names: list[str],
    *,
    scorer: FinancialSentimentScorer | None = None,
    limit_per_sector: int = 15,
) -> dict[str, Any]:
    """从已采集宏观/快讯语料按板块关键词聚合行业情绪指数（无额外 API）。"""
    if not news_pool or not sector_names:
        return {"sectors": [], "note": "empty_pool"}

    engine = scorer or FinancialSentimentScorer()
    rows: list[dict[str, Any]] = []
    for name in dict.fromkeys(sector_names):
        if not name:
            continue
        sa = engine.score_for_entity(news_pool, _sector_match_keywords(name), limit_per_sector)
        agg = sa.get("aggregate") or {}
        count = int(agg.get("count") or 0)
        if count <= 0:
            continue
        rows.append(
            {
                "sector": name,
                "score_100": agg.get("score_100"),
                "label": agg.get("label"),
                "count": count,
                "extreme": agg.get("extreme"),
                "event_distribution": agg.get("event_distribution") or {},
            }
        )

    rows.sort(
        key=lambda r: (abs(float(r.get("score_100") or 50) - 50), int(r.get("count") or 0)),
        reverse=True,
    )
    return {
        "sectors": rows[:12],
        "note": "基于宏观/快讯语料关键词匹配，非板块专用新闻接口",
    }


def build_macro_event_signals(macro_intel: dict[str, Any]) -> dict[str, Any]:
    """从宏观舆情 event_distribution 与经济日历提炼事件观察清单（供 LLM/摘要）。"""
    sent = macro_intel.get("sentiment_overview") or {}
    agg = sent.get("aggregate") or {}
    event_dist = agg.get("event_distribution") or {}
    watchlist: list[dict[str, Any]] = []

    for tag, count in sorted(event_dist.items(), key=lambda x: (-x[1], str(x[0])))[:8]:
        watchlist.append(
            {
                "event": EVENT_TAG_LABELS.get(str(tag), str(tag)),
                "tag": str(tag),
                "count": int(count),
                "importance": "high" if count >= 3 else "medium" if count >= 2 else "low",
                "source": "news_sentiment",
            }
        )

    seen_events: set[str] = {w["event"] for w in watchlist}
    for item in macro_intel.get("economic_calendar") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("event") or item.get("事件") or item.get("title") or "").strip()
        if not title or title in seen_events:
            continue
        date_str = str(item.get("日期") or item.get("date") or item.get("时间") or "")
        watchlist.append(
            {
                "event": title,
                "date": date_str or None,
                "importance": "medium",
                "source": "economic_calendar",
            }
        )
        seen_events.add(title)
        if len(watchlist) >= 10:
            break

    return {
        "watchlist": watchlist[:10],
        "dominant_tags": list(event_dist.keys())[:5],
        "extreme": agg.get("extreme"),
        "event_distribution": dict(event_dist),
    }


def assess_stock_crowding(
    code: str,
    *,
    hot_rank_records: list[dict[str, Any]] | None = None,
    market_comment: dict[str, Any] | None = None,
    xueqiu_hot: dict[str, Any] | None = None,
    participation_desire: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """个股拥挤度：人气榜 + 千股千评 + 雪球社交热度 + 参与意愿（中长线参考）。"""
    code = normalize_code(code)
    score = 0
    signals: list[str] = []

    for row in hot_rank_records or []:
        raw = str(row.get("代码") or row.get("股票代码") or "")
        if normalize_code(raw) != code:
            continue
        try:
            rank = int(row.get("当前排名") or row.get("排名") or 999)
        except (TypeError, ValueError):
            rank = 999
        if rank <= 5:
            score += 3
            signals.append(f"人气榜Top{rank}")
        elif rank <= 20:
            score += 2
            signals.append(f"人气榜Top{rank}")
        elif rank <= 50:
            score += 1
            signals.append(f"人气榜Top{rank}")
        break

    mc = market_comment or {}
    focus_idx = _safe_float(mc.get("关注指数"))
    if focus_idx is not None:
        if focus_idx >= 92:
            score += 2
            signals.append(f"关注指数{focus_idx:.1f}")
        elif focus_idx >= 85:
            score += 1
            signals.append(f"关注指数{focus_idx:.1f}")

    xq = xueqiu_hot or {}
    for key, label in (("deal", "雪球成交"), ("follow", "雪球关注")):
        row = xq.get(key) or {}
        if not row:
            continue
        try:
            rank = int(row.get("排名") or 999)
        except (TypeError, ValueError):
            rank = 999
        if rank <= 10:
            score += 2
            signals.append(f"{label}Top{rank}")
        elif rank <= 30:
            score += 1
            signals.append(f"{label}Top{rank}")

    pd_list = participation_desire or []
    if pd_list and isinstance(pd_list[-1], dict):
        latest = pd_list[-1]
        desire = _safe_float(latest.get("参与意愿"))
        chg = _safe_float(latest.get("参与意愿变化"))
        if desire is not None:
            if desire >= 75:
                score += 2
                signals.append(f"参与意愿{desire:.0f}")
            elif desire >= 65:
                score += 1
                signals.append(f"参与意愿{desire:.0f}")
        if chg is not None and chg >= 12:
            score += 1
            signals.append(f"参与意愿升{chg:.1f}%")

    if score >= 4:
        level = "high"
    elif score >= 2:
        level = "medium"
    else:
        level = "low"

    return {"crowding_risk": level, "crowding_score": score, "signals": signals}


def assess_sector_crowding(
    sector_name: str,
    *,
    hot_rank_records: list[dict[str, Any]] | None = None,
    top_n: int = 30,
) -> dict[str, Any]:
    """板块拥挤度：人气榜 TopN 中与板块名/龙头代码匹配的数量。"""
    from money_more.analysis.sector_map import infer_sector

    if not sector_name or not hot_rank_records:
        return {"crowding_risk": "unknown", "hot_hits": 0, "signals": []}

    hits = 0
    matched: list[str] = []
    for row in hot_rank_records[:top_n]:
        name = str(row.get("股票名称") or row.get("名称") or "")
        raw_code = str(row.get("代码") or row.get("股票代码") or "")
        code = normalize_code(raw_code)
        sector_hit = infer_sector(code) == sector_name
        if not sector_hit and sector_name and name:
            sector_hit = sector_name in name or (
                len(sector_name) >= 2 and sector_name[:2] in name
            )
        if sector_hit:
            hits += 1
            rank = row.get("当前排名") or row.get("排名")
            matched.append(f"{name}(Top{rank})")

    if hits >= 4:
        level = "high"
    elif hits >= 2:
        level = "medium"
    elif hits == 0:
        level = "low"
    else:
        level = "medium"

    signals = [f"人气榜Top{top_n}命中{hits}只"] + matched[:3]
    return {"crowding_risk": level, "hot_hits": hits, "signals": signals}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
