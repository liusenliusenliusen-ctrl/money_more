"""RSS 与财联社快讯采集。"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import akshare as ak
import feedparser
import requests

DEFAULT_RSS_FEEDS: list[dict[str, str]] = []  # 默认仅财联社直连；可在 config 中自定义 RSSHub 源

_DEFAULT_RSSHUB = "https://rsshub.app"


def feeds_from_rsshub_base(base: str | None = None) -> list[dict[str, str]]:
    """由 RSSHub base 生成财联社相关 feeds。"""
    root = (base or _DEFAULT_RSSHUB).rstrip("/")
    return [
        {"name": "财联社电报", "url": f"{root}/cls/telegraph", "category": "telegraph"},
        {"name": "财联社电报-加红", "url": f"{root}/cls/telegraph/red", "category": "telegraph_red"},
        {"name": "财联社深度", "url": f"{root}/cls/depth", "category": "depth"},
    ]


FALLBACK_RSS_FEEDS = feeds_from_rsshub_base(_DEFAULT_RSSHUB)

CLS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) money_more/0.2",
    "Referer": "https://www.cls.cn/telegraph",
}


def _parse_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    text = str(value).strip()
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).isoformat(timespec="seconds")
    except Exception:
        return text


def _normalize_item(
    title: str,
    content: str,
    published: str | None,
    source: str,
    category: str,
    url: str | None = None,
    level: str | None = None,
) -> dict[str, Any]:
    return {
        "title": title.strip(),
        "content": content.strip()[:2000],
        "published_at": published,
        "source": source,
        "category": category,
        "url": url,
        "level": level,
    }


class RssFeedFetcher:
    """RSS + 财联社直连 API 快讯采集。"""

    def __init__(
        self,
        feeds: list[dict[str, str]] | None = None,
        max_items_per_feed: int = 10,
        timeout: int = 8,
        cls_direct: bool = True,
        use_fallback_rss: bool = False,
        rsshub_base: str = "",
    ) -> None:
        if feeds:
            self.feeds = feeds
        elif use_fallback_rss:
            self.feeds = feeds_from_rsshub_base(rsshub_base or None)
        else:
            self.feeds = DEFAULT_RSS_FEEDS
        self.max_items_per_feed = max_items_per_feed
        self.timeout = timeout
        self.cls_direct = cls_direct
        self.rsshub_base = (rsshub_base or "").rstrip("/")
        self.use_fallback_rss = use_fallback_rss

    def fetch_all(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "feeds": [],
            "cls_telegraph": [],
            "cls_telegraph_important": [],
            "combined": [],
            "errors": [],
            "meta": {
                "cls_direct": self.cls_direct,
                "use_fallback_rss": self.use_fallback_rss,
                "rsshub_base": self.rsshub_base or "",
                "flash_sources_hit": [],
            },
        }

        if self.cls_direct:
            try:
                cls_all = self._fetch_cls_direct(important_only=False)
                cls_imp = self._fetch_cls_direct(important_only=True)
                result["cls_telegraph"] = cls_all
                result["cls_telegraph_important"] = cls_imp
                hits = sorted(
                    {
                        str(x.get("source") or "")
                        for x in (cls_all + cls_imp)
                        if x.get("source")
                    }
                )
                result["meta"]["flash_sources_hit"] = [h for h in hits if h]
            except Exception as exc:
                result["errors"].append(f"财联社直连: {exc}")

        for feed in self.feeds:
            name = feed.get("name", feed.get("url", "unknown"))
            url = feed["url"]
            category = feed.get("category", "rss")
            try:
                items = self._fetch_rss(url, name, category)
                result["feeds"].append({"name": name, "url": url, "items": items, "count": len(items)})
            except Exception as exc:
                result["errors"].append(f"RSS {name}: {exc}")

        combined: list[dict[str, Any]] = []
        combined.extend(result["cls_telegraph_important"])
        combined.extend(result["cls_telegraph"])
        for feed in result["feeds"]:
            combined.extend(feed.get("items") or [])

        # 去重（按标题）
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in combined:
            key = re.sub(r"\s+", "", item.get("title", ""))[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        result["combined"] = deduped
        return result

    def filter_by_keywords(self, items: list[dict[str, Any]], keywords: list[str], limit: int = 10) -> list[dict[str, Any]]:
        if not keywords:
            return items[:limit]
        matched: list[dict[str, Any]] = []
        for item in items:
            text = f"{item.get('title', '')} {item.get('content', '')}"
            if any(kw in text for kw in keywords if kw):
                matched.append(item)
        return matched[:limit]

    def _fetch_rss(self, url: str, source: str, category: str) -> list[dict[str, Any]]:
        resp = requests.get(
            url,
            headers={"User-Agent": CLS_HEADERS["User-Agent"]},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise RuntimeError(getattr(parsed, "bozo_exception", "RSS 解析失败"))
        items: list[dict[str, Any]] = []
        for entry in parsed.entries[: self.max_items_per_feed]:
            content = ""
            if hasattr(entry, "summary"):
                content = entry.summary
            elif hasattr(entry, "description"):
                content = entry.description
            content = re.sub(r"<[^>]+>", "", content)
            published = None
            if hasattr(entry, "published"):
                published = _parse_datetime(entry.published)
            elif hasattr(entry, "updated"):
                published = _parse_datetime(entry.updated)
            items.append(
                _normalize_item(
                    title=getattr(entry, "title", ""),
                    content=content,
                    published=published,
                    source=source,
                    category=category,
                    url=getattr(entry, "link", None),
                )
            )
        return items

    def _fetch_cls_direct(self, important_only: bool = False) -> list[dict[str, Any]]:
        # 优先用稳定源（财经早餐/同花顺/富途），财联社 AkShare 接口偶发超时
        items = self._fetch_flash_news_fallback(important_only)
        if len(items) >= max(3, self.max_items_per_feed // 2):
            return items
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._fetch_cls_via_akshare, important_only)
                cls_items = future.result(timeout=self.timeout)
                if cls_items:
                    return cls_items
        except (FuturesTimeout, Exception):
            pass
        return items

    def _fetch_cls_via_akshare(self, important_only: bool) -> list[dict[str, Any]]:
        symbol = "重点" if important_only else "全部"
        df = ak.stock_info_global_cls(symbol=symbol)
        if df is None or df.empty:
            return []
        items: list[dict[str, Any]] = []
        for _, row in df.head(self.max_items_per_feed).iterrows():
            title = str(row.get("标题") or "")
            content = str(row.get("内容") or "")
            pub_date = row.get("发布日期")
            pub_time = row.get("发布时间")
            published = None
            if pub_date is not None and pub_time is not None:
                published = f"{pub_date} {pub_time}"
            items.append(
                _normalize_item(
                    title=title,
                    content=content,
                    published=published,
                    source="财联社",
                    category="telegraph_important" if important_only else "telegraph",
                    level="A" if important_only else None,
                )
            )
        return items

    def _fetch_flash_news_fallback(self, important_only: bool) -> list[dict[str, Any]]:
        """财联社不可用时，用财经早餐/同花顺/富途快讯兜底。"""
        items: list[dict[str, Any]] = []
        sources = [
            ("财经早餐", ak.stock_info_cjzc_em, {"title": "标题", "content": "摘要", "time": "发布时间"}),
            ("同花顺快讯", ak.stock_info_global_ths, {"title": "标题", "content": "内容", "time": "发布时间"}),
            ("富途快讯", ak.stock_info_global_futu, {"title": "标题", "content": "内容", "time": "发布时间"}),
        ]
        per_source = max(2, self.max_items_per_feed // 2)
        for source_name, fn, cols in sources:
            try:
                df = fn()
                if df is None or df.empty:
                    continue
                for _, row in df.head(per_source).iterrows():
                    items.append(
                        _normalize_item(
                            title=str(row.get(cols["title"]) or ""),
                            content=str(row.get(cols["content"]) or ""),
                            published=str(row.get(cols["time"]) or ""),
                            source=source_name,
                            category="telegraph_important" if important_only else "telegraph",
                        )
                    )
            except Exception:
                continue
            if len(items) >= self.max_items_per_feed:
                break
        return items[: self.max_items_per_feed]
