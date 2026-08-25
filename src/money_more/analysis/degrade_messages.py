"""降级/备源统一话术（结论卡、台账、doctor 共用口径）。"""

from __future__ import annotations

from typing import Any

from money_more.data.ak_direct import classify_em_error

_SPOT_HINTS = {
    "em_all": "东财全量现货",
    "em_split": "东财分市场现货（全量失败后的次选）",
    "sina": "新浪现货备源（通常无 PE/PB，估值分已降权，非中性=齐备）",
    "cache": "进程内/当日缓存现货",
    "stale_cache": "过期磁盘缓存现货（可信度低，建议修通路后重跑）",
}


def spot_source_plain(source: str | None) -> str:
    src = str(source or "").strip()
    if not src:
        return "现货源未知"
    return _SPOT_HINTS.get(src, f"现货源=`{src}`")


def suggest_from_err_class(err_class: str | None) -> str:
    c = str(err_class or "other")
    return {
        "proxy": "疑似代理干扰 → 保持/开启 data.eastmoney_force_direct，或关闭系统代理后重试",
        "timeout": "超时 → 已有重试；仍失败则用备源，或错峰重跑",
        "empty": "返回空表 → 尝试分市场入口或新浪备源",
        "http": "HTTP/限流 → 降频、依赖缓存，稍后重跑",
        "other": "查 doctor 明细与网络",
    }.get(c, "查 doctor 明细")


def first_err_class_from_messages(messages: list[str] | None) -> str | None:
    for m in messages or []:
        text = str(m)
        if "[" in text and "]" in text:
            # annotate_em_error 格式 prefix[class]: ...
            try:
                mid = text.split("[", 1)[1].split("]", 1)[0]
                if mid in ("proxy", "timeout", "empty", "http", "other"):
                    return mid
            except Exception:
                pass
        cls = classify_em_error(text)
        if cls != "other":
            return cls
    return None


def build_screen_degrade_note(screen: dict[str, Any]) -> str:
    """筛股/行情降级一行说明。"""
    bits: list[str] = []
    src = screen.get("spot_source")
    if src and str(src) not in ("em_all",):
        bits.append(spot_source_plain(str(src)))
    err_cls = first_err_class_from_messages(list(screen.get("errors") or []))
    if err_cls:
        bits.append(f"错误类={err_cls}")
        bits.append(suggest_from_err_class(err_cls))
    if screen.get("degraded") and not bits:
        bits.append(screen.get("plain_note") or "遴选/行情降级")
    return "；".join(bits)


def flash_chain_tip() -> str:
    return "快讯推荐拓扑：早餐/东财全球 → CLS(短超时) →（可选）自建 RSSHub；公网 rsshub.app 默认关"
