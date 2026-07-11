"""财经新闻舆情打分：词典 + 规则 + 事件识别。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


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

EVENT_PATTERNS: list[tuple[str, float, str]] = [
    (r"降准|降息|下调LPR", 1.2, "macro_positive"),
    (r"加息|加准|收紧", -1.0, "macro_negative"),
    (r"回购|增持|举牌", 1.0, "holder_positive"),
    (r"减持|清仓|质押爆仓", -1.2, "holder_negative"),
    (r"中标|签订|获订单", 0.9, "business_positive"),
    (r"立案|调查|处罚|警示", -1.3, "regulatory_negative"),
    (r"业绩预增|扭亏|超预期", 1.2, "earnings_positive"),
    (r"业绩预减|首亏|续亏|低于预期", -1.2, "earnings_negative"),
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
        return {
            "aggregate": {
                "score": round(agg_score, 4),
                "score_100": round(agg_100, 2),
                "label": self._label(agg_score),
                "count": len(scored_items),
                "distribution": self._distribution(labels),
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
