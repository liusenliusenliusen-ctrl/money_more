"""统一 as_of 日期：回放/指定日期时所有数据窗口以此为准。"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def parse_as_of(value: date | datetime | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def as_of_datetime(as_of: date) -> datetime:
    """用于需要 datetime 的 API；指定历史日时取当日末。"""
    today = date.today()
    if as_of >= today:
        return datetime.now()
    return datetime.combine(as_of, datetime.max.time().replace(microsecond=0))


def ymd(as_of: date, delta_days: int = 0) -> str:
    d = as_of + timedelta(days=delta_days)
    return d.strftime("%Y%m%d")


def ymd_hms(as_of: date, delta_days: int = 0) -> str:
    d = as_of + timedelta(days=delta_days)
    if d >= date.today() and delta_days >= 0:
        return datetime.now().strftime("%Y%m%d %H:%M:%S")
    return f"{d.strftime('%Y%m%d')} 23:59:59"


def recent_weekdays(as_of: date, count: int) -> list[str]:
    """近似交易日（工作日），不含法定假日。"""
    dates: list[str] = []
    day = as_of
    while len(dates) < count + 5:
        if day.weekday() < 5:
            dates.append(day.strftime("%Y%m%d"))
        day -= timedelta(days=1)
    return dates[: count + 2]


def parse_record_date(
    item: dict,
    date_keys: tuple[str, ...] = (
        "date",
        "日期",
        "发布时间",
        "pub_time",
        "datetime",
        "time",
        "ctime",
        "公告日期",
        "发布日期",
        "交易日",
        "trade_date",
    ),
) -> date | None:
    """从记录中解析日期，供新鲜度/日历判断复用。"""
    return _extract_date(item, date_keys)


def filter_records_by_date(
    records: list[dict],
    as_of: date,
    lookback_days: int = 7,
    date_keys: tuple[str, ...] = (
        "date",
        "日期",
        "发布时间",
        "pub_time",
        "datetime",
        "time",
        "ctime",
        "公告日期",
        "发布日期",
    ),
) -> list[dict]:
    """过滤过期新闻/日历条目；无法解析日期的条目保留（避免误杀）。"""
    if not records:
        return []
    cutoff = as_of - timedelta(days=lookback_days)
    kept: list[dict] = []
    for item in records:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        parsed = _extract_date(item, date_keys)
        if parsed is None:
            kept.append(item)
            continue
        if cutoff <= parsed <= as_of + timedelta(days=1):
            kept.append(item)
    return kept


def filter_calendar_upcoming(
    records: list[dict],
    as_of: date,
    ahead_days: int = 21,
    date_keys: tuple[str, ...] = ("date", "日期", "公布时间", "时间"),
) -> list[dict]:
    """经济日历：只保留 as_of 前后窗口内的事件。"""
    if not records:
        return []
    start = as_of - timedelta(days=3)
    end = as_of + timedelta(days=ahead_days)
    kept: list[dict] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        parsed = _extract_date(item, date_keys)
        if parsed is None:
            kept.append(item)
            continue
        if start <= parsed <= end:
            kept.append(item)
    return kept


def _extract_date(item: dict, date_keys: tuple[str, ...]) -> date | None:
    for key in date_keys:
        if key not in item:
            continue
        raw = item.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        candidates = [text[:19], text[:16], text[:10], text]
        for cand in candidates:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M",
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%Y%m%d",
                "%Y年%m月%d日",
            ):
                try:
                    return datetime.strptime(cand, fmt).date()
                except ValueError:
                    continue
        try:
            return date.fromisoformat(text[:10].replace("/", "-"))
        except ValueError:
            continue
    return None
