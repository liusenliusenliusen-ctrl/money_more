"""公开信息完备性：用可观察异常替代「内幕/操纵」指控。

status:
- public_info_sufficient: 公开信息与价格行为大体可对照
- gap_suspected: 价格/资金异动难以被公开信息解释 → 降置信度、偏观望
- unknown: 数据不足无法判断
"""

from __future__ import annotations

from typing import Any

from money_more.data.fetcher import _safe_float


def assess_info_completeness(
    code: str,
    ak_snap: dict[str, Any] | None,
    tushare_bundle: dict[str, Any] | None = None,
    cross_check: dict[str, Any] | None = None,
    hard_gates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ak_snap = ak_snap or {}
    ts = tushare_bundle or {}
    xcheck = cross_check or {}
    gates = hard_gates or {}
    intel = ak_snap.get("intelligence") or {}

    reasons: list[str] = []
    unexplained: list[str] = []
    supporting: list[str] = []
    haircut = 0.0

    hist = ak_snap.get("history") or {}
    quote = ak_snap.get("quote") or {}
    chg = _safe_float(hist.get("change_pct") or quote.get("涨跌幅"))
    atr = _safe_float(hist.get("atr_pct_20d"))
    vol = _safe_float(hist.get("volume") or quote.get("成交量"))

    news_n = _count_news(ak_snap, intel, ts)
    ann_n = len(ts.get("announcements") or [])
    forecast_n = len(ts.get("forecast") or [])
    has_public = news_n + ann_n + forecast_n > 0
    if has_public:
        supporting.append(f"公开材料: 新闻≈{news_n} 公告={ann_n} 预告={forecast_n}")
    else:
        reasons.append("近窗公开新闻/公告/预告偏少")

    # 大幅波动但缺乏公开解释
    big_move = False
    if chg is not None and abs(chg) >= 7:
        big_move = True
    if atr is not None and chg is not None and abs(chg) >= max(5.0, atr * 1.8):
        big_move = True
    if big_move and not has_public:
        unexplained.append(f"价格异动 change_pct={chg} 且公开信息稀薄")
        haircut += 0.12
    elif big_move and news_n + ann_n == 0:
        unexplained.append(f"价格异动 change_pct={chg} 未见同步新闻/公告")
        haircut += 0.08

    # 双源冲突 → 信息质量存疑
    if xcheck.get("ok") is False:
        unexplained.append("双源交叉校验不一致: " + ",".join(xcheck.get("flags") or [])[:80])
        haircut += float(xcheck.get("confidence_haircut") or 0.05)

    # 成交量异常
    if vol is not None and vol <= 0:
        unexplained.append("成交量≈0，公开连续竞价信息可能不可用")
        haircut += 0.1
    fund = ak_snap.get("fund_flow") or {}
    net5 = _safe_float(fund.get("net_5d") or fund.get("net_3d"))
    if net5 is not None and chg is not None and abs(net5) > 0 and abs(chg) >= 5:
        # 资金与涨跌极端并存且无公告时记一笔
        if not has_public:
            unexplained.append("资金流与涨跌幅同向极端，但缺少公告解释")
            haircut += 0.05

    if gates.get("force_watch") or gates.get("block_buy"):
        reasons.append("硬门禁已触发: " + "; ".join((gates.get("reasons") or [])[:2]))

    # 研报/龙虎等「市场侧」信息有时反而说明交易拥挤，不算完备解释
    lhb = intel.get("lhb_records") or intel.get("lhb") or intel.get("dragon_tiger") or []
    if lhb and big_move and news_n + ann_n == 0:
        unexplained.append("有龙虎/席位痕迹但缺少公司层公开说明（信息缺口，非指控）")
        haircut += 0.06

    haircut = round(min(0.35, haircut), 3)
    if not ak_snap.get("history") and not quote:
        status = "unknown"
        severity = "medium"
    elif unexplained and haircut >= 0.12:
        status = "gap_suspected"
        severity = "high" if haircut >= 0.2 or (big_move and not has_public) else "medium"
    elif unexplained:
        status = "gap_suspected"
        severity = "low"
    elif has_public or (chg is not None and abs(chg) < 5):
        status = "public_info_sufficient"
        severity = "low"
    else:
        status = "unknown"
        severity = "low"

    action_hint = None
    if status == "gap_suspected" and severity in ("high", "medium"):
        action_hint = "watch"
    note = {
        "public_info_sufficient": "公开信息与价格行为大体可对照。",
        "gap_suspected": "公开信息不足以解释部分异动→降置信度，偏观望（仅标记信息缺口）。",
        "unknown": "公开信息覆盖不足，完备性无法判断。",
    }.get(status, "")

    return {
        "code": code,
        "status": status,
        "severity": severity,
        "confidence_haircut": haircut,
        "action_hint": action_hint,
        "unexplained": unexplained[:5],
        "supporting": supporting[:4],
        "reasons": reasons[:4],
        "note": note,
        "layer": "info_completeness",
    }


def _count_news(ak_snap: dict[str, Any], intel: dict[str, Any], ts: dict[str, Any]) -> int:
    n = 0
    n += len(ak_snap.get("news") or [])
    n += len(intel.get("news") or [])
    n += len(intel.get("rss_matches") or [])
    n += len(ts.get("news") or [])
    return n
