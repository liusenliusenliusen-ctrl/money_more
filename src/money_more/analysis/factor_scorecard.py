"""结构化因子评分卡：确定性打分，供决策加权，不依赖 LLM 散文。"""

from __future__ import annotations

from typing import Any

from money_more.analysis.valuation import (
    blend_valuation_with_dividend,
    valuation_score_from_percentiles,
)


DEFAULT_WEIGHTS = {
    "valuation": 0.25,
    "momentum": 0.10,
    "fund_flow": 0.10,
    "sentiment": 0.10,
    "quality": 0.30,
    "narrative": 0.15,
}


def build_stock_scorecard(
    stock_snap: dict[str, Any],
    stock_analysis: dict[str, Any] | None = None,
    stock_intel: dict[str, Any] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """从快照/情报/LLM 分析提取 0–100 因子分。"""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    intel = stock_intel or stock_snap.get("intelligence") or {}
    analysis = stock_analysis or {}
    hist = stock_snap.get("history") or {}
    quote = stock_snap.get("quote") or {}
    tushare = intel.get("tushare") or {}
    valuation = tushare.get("valuation") or {}
    latest_val = valuation.get("latest") or {}

    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {k: [] for k in w}

    # --- valuation: 优先历史分位（中长线），回退绝对 PE/PB；并入股息率 ---
    pe = _f(latest_val.get("pe") or latest_val.get("pe_ttm") or quote.get("市盈率-动态") or quote.get("市盈率"))
    pb = _f(latest_val.get("pb") or quote.get("市净率"))
    percentiles = valuation.get("percentiles") or {}
    pe_pct = _f(percentiles.get("pe_percentile"))
    pb_pct = _f(percentiles.get("pb_percentile"))
    dv_ratio = _f(
        percentiles.get("dv_ratio")
        or latest_val.get("dv_ratio")
        or latest_val.get("dv_ttm")
    )
    pct_score = valuation_score_from_percentiles(pe_pct, pb_pct)
    val_score = 40.0  # 缺失时低置信，不用 50 伪装中性（S6）
    valuation_known = False
    if pct_score is not None:
        valuation_known = True
        val_score = pct_score
        if pe_pct is not None:
            evidence["valuation"].append(f"PE历史分位{pe_pct:.0f}%")
        if pb_pct is not None:
            evidence["valuation"].append(f"PB历史分位{pb_pct:.0f}%")
        if percentiles.get("label"):
            evidence["valuation"].append(f"估值锚={percentiles['label']}")
    elif pe is not None:
        valuation_known = True
        if pe <= 0:
            val_score = 25.0
            evidence["valuation"].append(f"PE={pe} 亏损/异常")
        elif pe < 15:
            val_score = 80.0
            evidence["valuation"].append(f"PE={pe:.1f} 偏低")
        elif pe < 30:
            val_score = 60.0
            evidence["valuation"].append(f"PE={pe:.1f} 中性")
        elif pe < 60:
            val_score = 40.0
            evidence["valuation"].append(f"PE={pe:.1f} 偏高")
        else:
            val_score = 25.0
            evidence["valuation"].append(f"PE={pe:.1f} 很高")
        if pb is not None and pb > 0:
            if pb < 1.5:
                val_score = min(100.0, val_score + 10)
            elif pb > 8:
                val_score = max(0.0, val_score - 15)
            evidence["valuation"].append(f"PB={pb:.2f}")
    else:
        evidence["valuation"].append("估值数据缺失，低置信")
    val_score, dv_ev = blend_valuation_with_dividend(val_score, dv_ratio)
    if dv_ev:
        valuation_known = True
    evidence["valuation"].extend(dv_ev)
    scores["valuation"] = _clamp(val_score)

    # --- momentum: 中长线用 MA20 / 20d 区间 / 相对强度（去掉日涨跌与 MA5，S10）---
    mom = 50.0
    above = hist.get("above_ma20")
    close = _f(hist.get("close") or quote.get("最新价"))
    if above is True:
        mom += 15
        evidence["momentum"].append("站上MA20")
    elif above is False:
        mom -= 15
        evidence["momentum"].append("跌破MA20")
    high20 = _f(hist.get("high_20d"))
    low20 = _f(hist.get("low_20d"))
    if close and high20 and low20 and high20 > low20:
        pos = (close - low20) / (high20 - low20)
        mom += (pos - 0.5) * 20
        evidence["momentum"].append(f"20日区间位置{pos:.0%}")
    rs = _f(hist.get("rs_vs_hs300_20d"))
    if rs is not None:
        mom += max(-15, min(15, rs))
        evidence["momentum"].append(f"相对沪深300_20d={rs:.1f}%")
    scores["momentum"] = _clamp(mom)

    # --- fund_flow: 个股资金/两融/北向 ---
    flow = 50.0
    fund = stock_snap.get("fund_flow") or intel.get("fund_flow") or {}
    if fund:
        net3 = _f(fund.get("net_3d") or fund.get("main_net_3d"))
        net5 = _f(fund.get("net_5d") or fund.get("main_net_5d"))
        if net5 is not None:
            # 假设单位万元，粗略映射
            flow += max(-30, min(30, net5 / 5000 * 10))
            evidence["fund_flow"].append(f"主力净流入5d≈{net5}")
        if net3 is not None and net5 is not None and net3 * net5 < 0:
            flow -= 10
            evidence["fund_flow"].append("短中期资金背离")
    margin = intel.get("margin_detail") or []
    if margin:
        evidence["fund_flow"].append("有两融明细")
        flow += 5
    hk = intel.get("northbound_hold") or fund.get("northbound")
    if hk:
        evidence["fund_flow"].append("有北向持仓信息")
        flow += 5
        # 若有持股变动字段，粗略加减分
        chg_hold = _f(hk.get("今日增持估计-股数") or hk.get("增持估计") or hk.get("持股数量变化"))
        if chg_hold is not None:
            if chg_hold > 0:
                flow += 8
                evidence["fund_flow"].append("北向增持")
            elif chg_hold < 0:
                flow -= 8
                evidence["fund_flow"].append("北向减持")
    scores["fund_flow"] = _clamp(flow)

    # --- sentiment: 拥挤惩罚为主；新闻语调/热度不抬分（S3）；分列供报告（S13）---
    sent = 50.0
    sa = intel.get("sentiment_analysis") or {}
    agg = sa.get("aggregate") or {}
    s100 = _f(agg.get("score_100"))
    news_tone: float | None = s100
    if s100 is not None:
        evidence["sentiment"].append(f"新闻语调{s100}(不抬分)")
        if s100 < 30:
            sent -= min(10.0, (30.0 - s100) * 0.25)
            evidence["sentiment"].append("语调偏冷轻量减分")
    rating = _f((intel.get("sentiment_scores") or {}).get("latest_rating"))
    if rating is not None:
        evidence["sentiment"].append(f"市场评分{rating}(旁证不抬分)")
    extreme = str(agg.get("extreme") or "").lower()
    if extreme in ("greed", "euphoria", "过热", "贪婪"):
        sent -= 12
        evidence["sentiment"].append(f"极端情绪:{agg.get('extreme')}→拥挤惩罚")
    elif extreme in ("fear", "panic", "恐慌"):
        evidence["sentiment"].append(f"极端情绪:{agg.get('extreme')}(不抄底加分)")
    crowding = intel.get("crowding_signal") or {}
    cr = str(crowding.get("crowding_risk") or "") or "unknown"
    crowding_score = crowding.get("crowding_score")
    if cr == "high":
        sent -= 18
        evidence["sentiment"].append("量化拥挤度高")
    elif cr == "medium":
        sent -= 8
        evidence["sentiment"].append("量化拥挤度中")
    elif cr == "low":
        evidence["sentiment"].append("量化拥挤度低")
    pd_list = intel.get("participation_desire") or []
    if pd_list and isinstance(pd_list[-1], dict):
        desire = _f(pd_list[-1].get("参与意愿"))
        if desire is not None:
            evidence["sentiment"].append(f"参与意愿{desire:.0f}(拥挤旁证)")
            if desire >= 70:
                sent -= 6
                evidence["sentiment"].append("参与意愿偏高→减分")
    xq = intel.get("xueqiu_hot") or {}
    deal_rank = _f((xq.get("deal") or {}).get("排名"))
    if deal_rank is not None and deal_rank <= 20:
        sent -= 5
        evidence["sentiment"].append(f"雪球成交Top{int(deal_rank)}→拥挤减分")
    scores["sentiment"] = _clamp(sent)
    sentiment_breakdown = {
        "news_tone": round(news_tone, 1) if news_tone is not None else None,
        "crowding_risk": cr,
        "crowding_score": crowding_score,
        "factor_score": round(scores["sentiment"], 1),
        "note": "拥挤惩罚进因子分；新闻语调不抬分",
    }

    # --- quality: 财务粗指标 + 经营现金流覆盖 ---
    qual = 50.0
    fina = (tushare.get("financials") or {}).get("indicators") or []
    if fina:
        row = fina[0] if isinstance(fina[0], dict) else {}
        roe = _f(row.get("roe") or row.get("roe_waa"))
        gross = _f(row.get("grossprofit_margin") or row.get("gross_margin"))
        if roe is not None:
            if roe > 15:
                qual += 20
            elif roe > 8:
                qual += 10
            elif roe < 0:
                qual -= 20
            evidence["quality"].append(f"ROE={roe}")
        if gross is not None:
            if gross > 40:
                qual += 10
            elif gross < 15:
                qual -= 10
            evidence["quality"].append(f"毛利率={gross}")
    else:
        abstract = (stock_snap.get("financial") or {}).get("abstract") or []
        if abstract:
            evidence["quality"].append("有财务摘要")
            qual += 5
    ocf = stock_snap.get("ocf_quality") or intel.get("ocf_quality") or {}
    ocf_signal = str(ocf.get("signal") or "")
    ocf_avg = _f(ocf.get("ocf_to_profit_avg") or ocf.get("ocf_to_profit"))
    if ocf_signal == "strong":
        qual += 12
        evidence["quality"].append("现金流质量强")
    elif ocf_signal == "adequate":
        qual += 4
        evidence["quality"].append("现金流质量尚可")
    elif ocf_signal == "weak":
        qual -= 18
        evidence["quality"].append("现金流质量弱")
        if ocf.get("ni_ocf_divergence"):
            qual -= 8
            evidence["quality"].append("利润与经营现金流背离")
    if ocf_avg is not None:
        evidence["quality"].append(f"OCF/净利润≈{ocf_avg:.2f}")
    scores["quality"] = _clamp(qual)

    # --- narrative: LLM research_rating ---
    narr = 50.0
    rating_map = {
        "strong_buy": 90,
        "buy": 75,
        "accumulate": 70,
        "hold": 50,
        "reduce": 30,
        "sell": 15,
        "avoid": 10,
        "强烈推荐": 90,
        "推荐": 75,
        "增持": 70,
        "中性": 50,
        "减持": 30,
        "卖出": 15,
    }
    rr = str(analysis.get("research_rating") or "").lower().strip()
    for k, v in rating_map.items():
        if k in rr or rr == k:
            narr = float(v)
            evidence["narrative"].append(f"research_rating={analysis.get('research_rating')}")
            break
    conf = _f(analysis.get("confidence"))
    if conf is not None:
        narr = narr * (0.6 + 0.4 * conf)
        evidence["narrative"].append(f"LLM置信度{conf}")
    scores["narrative"] = _clamp(narr)

    # 加权总分：估值未知时半权，避免中性伪装挤进前列（S6）
    effective_w = dict(w)
    if not valuation_known:
        effective_w["valuation"] = effective_w.get("valuation", 0) * 0.5
        evidence["valuation"].append("估值权重减半")
    wsum = sum(effective_w.values()) or 1.0
    total = sum(scores[k] * effective_w.get(k, 0) for k in scores) / wsum

    return {
        "scores": {k: round(v, 1) for k, v in scores.items()},
        "weights": w,
        "effective_weights": {k: round(v, 4) for k, v in effective_w.items()},
        "valuation_known": valuation_known,
        "sentiment_breakdown": sentiment_breakdown,
        "total_score": round(total, 1),
        "evidence": evidence,
        "signal": _signal_from_total(total),
    }


def _signal_from_total(total: float) -> str:
    if total >= 70:
        return "bullish"
    if total >= 55:
        return "constructive"
    if total >= 45:
        return "neutral"
    if total >= 30:
        return "cautious"
    return "bearish"


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))
