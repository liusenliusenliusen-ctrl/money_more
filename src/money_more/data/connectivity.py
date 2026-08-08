"""数据源连通探测：东财双轨、快讯、板块资金等（供 doctor / 台账）。"""

from __future__ import annotations

import time
from typing import Any, Callable

import akshare as ak

from money_more.data.ak_direct import (
    classify_em_error,
    eastmoney_bypass_mode,
    eastmoney_direct_session,
    eastmoney_force_direct_enabled,
)
from money_more.data.fetcher import (
    _canonicalize_spot_df,
    fetch_hot_rank_with_fallback,
    fetch_sector_board_summary,
)


def _timed(fn: Callable[[], Any]) -> tuple[Any, float, str | None, str | None]:
    t0 = time.perf_counter()
    try:
        val = fn()
        ms = (time.perf_counter() - t0) * 1000
        return val, ms, None, None
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return None, ms, classify_em_error(exc), str(exc)[:200]


def probe_eastmoney_spot() -> dict[str, Any]:
    """对比：当前环境 vs force_direct 会话，各打一枪 spot_em。"""

    def _call_spot() -> int:
        raw = ak.stock_zh_a_spot_em()
        df = _canonicalize_spot_df(raw)
        if df is None or df.empty:
            raise RuntimeError("spot_empty")
        return int(len(df))

    # 轨 1：不强制清代理（临时关 force）
    with eastmoney_direct_session(False):
        n1, ms1, err1, detail1 = _timed(_call_spot)
    # 轨 2：强制直连
    with eastmoney_direct_session(True):
        n2, ms2, err2, detail2 = _timed(_call_spot)

    env_ok = n1 is not None and int(n1) > 0
    direct_ok = n2 is not None and int(n2) > 0
    if direct_ok and not env_ok:
        recommend = "keep_force_direct"
        tip = "走代理失败、直连成功 → 保持 data.eastmoney_force_direct=true"
    elif env_ok and direct_ok:
        recommend = "either_ok"
        tip = "两轨均可；可保持 force_direct 以避代理抖动"
    elif env_ok and not direct_ok:
        recommend = "try_disable_force_direct"
        tip = "仅走代理成功 → 检查是否必须代理出网，可试 force_direct=false"
    else:
        recommend = "network_or_mirror"
        tip = "两轨皆失败 → 非仅代理问题，查网络/东财入口/防火墙"

    return {
        "env_proxy": {
            "ok": env_ok,
            "rows": n1,
            "latency_ms": round(ms1, 1),
            "err_class": err1,
            "detail": detail1,
        },
        "force_direct": {
            "ok": direct_ok,
            "rows": n2,
            "latency_ms": round(ms2, 1),
            "err_class": err2,
            "detail": detail2,
        },
        "config_force_direct": eastmoney_force_direct_enabled(),
        "bypass_mode": eastmoney_bypass_mode(),
        "recommend": recommend,
        "tip": tip,
    }


def probe_hist_sample(code: str = "600519") -> dict[str, Any]:
    from money_more.data.fetcher import MarketDataFetcher
    from datetime import date, timedelta

    fetcher = MarketDataFetcher(as_of=date.today())
    end = date.today()
    start = end - timedelta(days=40)

    def _call() -> int:
        df = fetcher._fetch_daily_hist(
            code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        )
        if df is None or df.empty:
            raise RuntimeError("hist_empty")
        return int(len(df))

    n, ms, err, detail = _timed(_call)
    return {
        "code": code,
        "ok": n is not None,
        "rows": n,
        "latency_ms": round(ms, 1),
        "err_class": err,
        "detail": detail,
    }


def probe_hot_rank() -> dict[str, Any]:
    def _call() -> tuple[int, str]:
        df, source, errors = fetch_hot_rank_with_fallback(limit=20)
        if df is None or df.empty:
            raise RuntimeError("; ".join(errors) or "hot_empty")
        return int(len(df)), source or ""

    t0 = time.perf_counter()
    try:
        n, source = _call()
        return {
            "ok": True,
            "rows": n,
            "source": source,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "err_class": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "rows": 0,
            "source": "",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "err_class": classify_em_error(exc),
            "detail": str(exc)[:200],
        }


def probe_sector_flow() -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        df, source, errors = fetch_sector_board_summary()
        ok = df is not None and not df.empty
        return {
            "ok": ok,
            "rows": int(len(df)) if ok else 0,
            "source": source or "",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "err_class": None if ok else "empty",
            "errors": errors[:4],
        }
    except Exception as exc:
        return {
            "ok": False,
            "rows": 0,
            "source": "",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "err_class": classify_em_error(exc),
            "detail": str(exc)[:200],
        }


def probe_flash_news(rss_fetcher: Any | None) -> dict[str, Any]:
    if rss_fetcher is None:
        return {"ok": False, "detail": "rss.enabled=false", "err_class": "disabled"}
    t0 = time.perf_counter()
    try:
        payload = rss_fetcher.fetch_all()
        breakfast = [
            x
            for x in (payload.get("cls_telegraph") or [])
            if "早餐" in str(x.get("source") or "")
            or "cjzc" in str(x.get("source") or "").lower()
            or "东财" in str(x.get("source") or "")
            or "同花顺" in str(x.get("source") or "")
            or "富途" in str(x.get("source") or "")
        ]
        n_all = len(payload.get("cls_telegraph") or [])
        n_imp = len(payload.get("cls_telegraph_important") or [])
        n_rss = sum(int(f.get("count") or 0) for f in (payload.get("feeds") or []))
        errors = list(payload.get("errors") or [])
        return {
            "ok": n_all > 0 or n_rss > 0,
            "telegraph": n_all,
            "important": n_imp,
            "rss_items": n_rss,
            "breakfast_like": len(breakfast),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "errors": errors[:4],
            "err_class": classify_em_error(errors[0]) if errors and n_all == 0 else None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "err_class": classify_em_error(exc),
            "detail": str(exc)[:200],
        }


def format_doctor_table(rows: list[dict[str, Any]]) -> str:
    """纯文本表，避免 doctor 强依赖 rich Table。"""
    if not rows:
        return "(empty)"
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    head = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    lines = [head, sep]
    for r in rows:
        lines.append(" | ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    return "\n".join(lines)
