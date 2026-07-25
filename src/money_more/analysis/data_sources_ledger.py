"""本轮数据源台账：连接了谁、拿了什么、是否成功、用在哪。"""

from __future__ import annotations

from typing import Any

from money_more.data.fetcher import sector_money_flow_present


def _err_has(errors: list[Any], *needles: str) -> bool:
    text = " ".join(str(e) for e in errors)
    return any(n in text for n in needles)


def _status_icon(status: str) -> str:
    return {
        "ok": "✅",
        "fallback": "⚠️",
        "degraded": "⚠️",
        "fail": "❌",
        "empty": "❌",
        "skipped": "➖",
    }.get(status, "❓")


def build_data_sources_ledger(result: dict[str, Any]) -> dict[str, Any]:
    """从 pipeline result 汇总可读的数据源台账（不发起网络请求）。"""
    macro = ((result.get("intelligence") or {}).get("macro_raw") or {})
    errors = list(macro.get("errors") or [])
    dq = result.get("data_quality") or {}
    screen = result.get("screen") or {}
    gl = macro.get("global_liquidity") or {}
    rows: list[dict[str, Any]] = []

    def add(
        *,
        name: str,
        provider: str,
        fetches: str,
        status: str,
        detail: str,
        used_in: str,
    ) -> None:
        rows.append(
            {
                "name": name,
                "provider": provider,
                "fetches": fetches,
                "status": status,
                "obtained": status in ("ok", "fallback", "degraded"),
                "detail": detail,
                "used_in": used_in,
                "icon": _status_icon(status),
            }
        )

    # —— 行情 / 筛股 ——
    spot_src = str(screen.get("spot_source") or "")
    raw_n = int(screen.get("universe_size_raw") or 0)
    uni_n = int(screen.get("universe_size") or 0)
    quant_n = int(screen.get("quant_size") or 0)
    deep_n = int(screen.get("deep_size") or 0)
    if screen.get("enabled") is False:
        add(
            name="全 A 现货快照",
            provider="东财 / 新浪（未启用筛股）",
            fetches="全市场价格、涨跌幅、成交额等，构成选股宇宙",
            status="skipped",
            detail="screen.enabled=false，本轮未跑全市场漏斗",
            used_in="关闭时深度池仅必跟/持仓名单",
        )
    elif screen.get("ok") and raw_n > 0:
        if spot_src and spot_src not in ("em_all", "cache", ""):
            add(
                name="全 A 现货快照",
                provider=f"主源东财 push2 → 备源（本轮={spot_src}）",
                fetches="全市场价格、涨跌幅、成交额（新浪备源通常无 PE/PB）",
                status="fallback",
                detail=f"已获取约 {raw_n} 只；过滤后 {uni_n} → 量化 {quant_n} → 深度 {deep_n}",
                used_in="§筛股漏斗 → 深度池 → §3 决策链 / §4 动作；备源时估值因子中性处理",
            )
        else:
            add(
                name="全 A 现货快照",
                provider="东财 stock_zh_a_spot_em" + (f"（{spot_src}）" if spot_src else ""),
                fetches="全市场价格、涨跌幅、成交额、估值等",
                status="ok",
                detail=f"已获取约 {raw_n} 只；过滤后 {uni_n} → 量化 {quant_n} → 深度 {deep_n}",
                used_in="§筛股漏斗 → 深度池 → §3 决策链 / §4 动作",
            )
    else:
        add(
            name="全 A 现货快照",
            provider="东财 push2 → 分市场 → 新浪 → 磁盘缓存",
            fetches="全市场价格、涨跌幅、成交额等，构成选股宇宙",
            status="fail",
            detail=screen.get("plain_note")
            or screen.get("note")
            or ("；".join(str(e) for e in (screen.get("errors") or [])[:3]) or "spot 失败/空"),
            used_in="失败则深度池只剩必跟名单，结论可信度下调并收紧开仓",
        )

    # —— 板块资金 ——
    flow = macro.get("sector_money_flow")
    flow_src = str(macro.get("sector_money_flow_source") or "")
    if sector_money_flow_present(flow):
        add(
            name="行业/板块资金流",
            provider=flow_src or "同花顺 / 东财（多源回退）",
            fetches="行业涨跌幅、主力净流入排名",
            status="ok" if flow_src in ("", "ths_summary", "ths_flow") else "fallback",
            detail=f"来源标记 `{flow_src or 'unknown'}`，已写入板块资金摘要",
            used_in="§2 板块优先级 / 结论卡板块态度 / 叙事与风格判断",
        )
    else:
        add(
            name="行业/板块资金流",
            provider="同花顺摘要 → 同花顺资金流 → 东财板块资金流",
            fetches="行业涨跌幅、主力净流入排名",
            status="fail",
            detail="三源均未形成有效资金流表",
            used_in="板块排序缺少硬资金确认，更多依赖舆情与指数结构",
        )

    # —— 北向 / 两融 ——
    nb = macro.get("northbound_summary")
    nb_f = macro.get("northbound_freshness") or {}
    if nb and nb_f.get("stale") is not True:
        add(
            name="北向资金",
            provider="东财沪深港通",
            fetches="北向成交/净流入摘要",
            status="ok",
            detail=f"最新日 {nb_f.get('latest_date') or '-'}，滞后约 {nb_f.get('staleness_days', '?')} 天",
            used_in="§1 流动性与风险偏好；与全球流动性交叉",
        )
    elif nb:
        add(
            name="北向资金",
            provider="东财沪深港通",
            fetches="北向成交/净流入摘要",
            status="degraded",
            detail=f"有数据但偏旧（latest={nb_f.get('latest_date')}, stale={nb_f.get('stale')}）",
            used_in="仅作背景，不单独驱动加减仓",
        )
    else:
        add(
            name="北向资金",
            provider="东财沪深港通",
            fetches="北向成交/净流入摘要",
            status="fail",
            detail="未取到有效摘要",
            used_in="§1 流动性段落会弱化外资维度",
        )

    if macro.get("margin_trend") or macro.get("margin_trend_sz"):
        add(
            name="融资融券",
            provider="宏观两融序列（沪/深）",
            fetches="融资余额等杠杆情绪",
            status="ok",
            detail="已取到两融趋势序列",
            used_in="§1 风险偏好 / 去杠杆或加杠杆判断",
        )
    else:
        add(
            name="融资融券",
            provider="宏观两融序列（沪/深）",
            fetches="融资余额等杠杆情绪",
            status="fail",
            detail="未取到两融趋势",
            used_in="缺少杠杆维度时更依赖跌停潮/成交结构",
        )

    # —— 新闻 / 政策 / 快讯 ——
    if macro.get("policy_news"):
        add(
            name="政策/联播类新闻",
            provider="Tushare CCTV → AkShare news_cctv",
            fetches="政策导向、联播要点",
            status="degraded" if macro.get("policy_news_stale") else "ok",
            detail="使用了偏旧缓存回退" if macro.get("policy_news_stale") else f"约 {len(macro.get('policy_news') or [])} 条",
            used_in="§0 情报主题 / 政策市侧栏假说",
        )
    else:
        add(
            name="政策/联播类新闻",
            provider="Tushare CCTV → AkShare news_cctv",
            fetches="政策导向、联播要点",
            status="fail",
            detail="主源与回退皆空",
            used_in="政策叙事置信度下调",
        )

    g_em = bool(macro.get("global_news"))
    g_sina = bool(macro.get("global_news_sina"))
    if g_em or g_sina:
        parts = []
        if g_em:
            parts.append(f"东财{len(macro.get('global_news') or [])}条")
        if g_sina:
            parts.append(f"新浪{len(macro.get('global_news_sina') or [])}条")
        add(
            name="全球/财经快讯",
            provider="东财 global_em + 新浪 global_sina",
            fetches="盘面新闻、宏观突发摘要",
            status="ok",
            detail="；".join(parts),
            used_in="§0 情报综述、舆情打分语料",
        )
    else:
        add(
            name="全球/财经快讯",
            provider="东财 / 新浪",
            fetches="盘面新闻、宏观突发摘要",
            status="fail",
            detail="两侧快讯皆空",
            used_in="情报主题可能偏薄",
        )

    flash = bool(macro.get("rss_telegraph") or macro.get("rss_important") or macro.get("rss_feeds"))
    if flash:
        add(
            name="电报/早餐快讯",
            provider="东财财经早餐 / 同花顺 / 富途（财联社易挂起）",
            fetches="盘中电报、早餐要点",
            status="ok",
            detail=f"telegraph≈{len(macro.get('rss_telegraph') or [])} · important≈{len(macro.get('rss_important') or [])}",
            used_in="§0 舆情；降权短线噪声后仍可作事件线索",
        )
    else:
        cls_hang = _err_has(errors, "cls", "财联社", "stock_info_global_cls")
        add(
            name="电报/早餐快讯",
            provider="东财早餐 / 同花顺 / 富途 / 财联社 / RSSHub",
            fetches="盘中电报、早餐要点",
            status="fail",
            detail="未取到快讯" + ("（财联社路径已知易超时）" if cls_hang else ""),
            used_in="事件驱动线索减少；主线更多依赖硬宏观与资金流",
        )

    # —— 宏观日历 / 硬指标 / 全球流动性 ——
    cal_primary = bool(macro.get("economic_calendar"))
    cal_alt = bool(macro.get("economic_calendar_alt") or macro.get("economic_calendar_synthetic"))
    if cal_primary and not _err_has(errors, "economic_calendar_primary_empty"):
        add(
            name="经济日历",
            provider="百度经济日历",
            fetches="即将公布的宏观数据日程",
            status="ok",
            detail=f"约 {len(macro.get('economic_calendar') or [])} 条",
            used_in="§0/§1 事件观察清单",
        )
    elif cal_alt or cal_primary:
        add(
            name="经济日历",
            provider="百度主源 → 备用/由 PMI·CPI·M2 合成",
            fetches="宏观数据日程或近月硬指标快照",
            status="fallback",
            detail="主源空，已用备用或合成日历",
            used_in="仍可提示关注窗口，精度低于官方日历",
        )
    else:
        add(
            name="经济日历",
            provider="百度 / 备用合成",
            fetches="宏观数据日程",
            status="fail",
            detail="主源与备用皆空",
            used_in="缺少明确数据日催化提醒",
        )

    if macro.get("macro_hard"):
        add(
            name="国内宏观硬指标",
            provider="AkShare PMI / CPI / M2 等",
            fetches="景气、通胀、货币供应量",
            status="ok",
            detail="已写入 macro_hard",
            used_in="§1 中长线宏观背景；与风格（价值/成长）联动",
        )
    else:
        add(
            name="国内宏观硬指标",
            provider="AkShare PMI / CPI / M2 等",
            fetches="景气、通胀、货币供应量",
            status="fail",
            detail="macro_hard 为空",
            used_in="宏观判断更多依赖新闻叙事",
        )

    stance = str(gl.get("stance") or "unknown")
    if stance and stance != "unknown":
        add(
            name="全球流动性",
            provider="中美利率 bond_zh_us_rate + 中行美元汇率",
            fetches="美债收益率、期限利差、USD/CNY",
            status="ok",
            detail=f"stance=`{stance}`；源={','.join(gl.get('source') or []) or '-'}",
            used_in="§1.0 全球流动性；收紧时约束进攻仓位",
        )
    else:
        add(
            name="全球流动性",
            provider="中美利率 + USD/CNY",
            fetches="美债收益率、期限利差、USD/CNY",
            status="fail",
            detail="；".join(str(e) for e in (gl.get("errors") or [])[:2]) or "stance=unknown",
            used_in="缺少海外流动性主线时，A 股风险偏好判断更依赖内资",
        )

    # —— 情绪 ——
    sent = (macro.get("sentiment_overview") or {}).get("aggregate")
    hot_fail = _err_has(errors, "人气榜", "hot_rank", "push2.eastmoney.com")
    hot_fb = macro.get("hot_rank_source") in ("xueqiu_follow",) or _err_has(
        errors, "hot_rank_fallback", "xueqiu_follow"
    )
    if sent:
        provider = "新闻词典打分 + 千股千评"
        if hot_fb:
            provider += "（人气榜→雪球关注榜备源）"
        elif hot_fail:
            provider += "（人气榜依赖东财 push2）"
        add(
            name="舆情/情绪量化",
            provider=provider,
            fetches="综合舆情分、拥挤度线索、事件分布",
            status="ok" if hot_fb or not hot_fail else "degraded",
            detail=(
                f"score_100={sent.get('score_100')} label={sent.get('label')}"
                + (f"; extreme={sent.get('extreme')}" if sent.get("extreme") else "")
                + ("；人气榜已用雪球备源" if hot_fb else "")
                + ("；人气榜失败已降权" if hot_fail and not hot_fb else "")
                + (
                    f"；行业情绪指数{len((macro.get('industry_sentiment_index') or {}).get('sectors') or [])}板块"
                    if (macro.get("industry_sentiment_index") or {}).get("sectors")
                    else ""
                )
            ),
            used_in="§0/结论卡环境；与硬数据冲突时以硬数据为准",
        )
    else:
        add(
            name="舆情/情绪量化",
            provider="新闻词典 + 东财情绪接口",
            fetches="综合舆情分",
            status="fail",
            detail="aggregate 为空" + ("；人气榜失败" if hot_fail else ""),
            used_in="情绪维度缺失，避免单独用电报定调",
        )

    # —— Tushare ——
    ts_news = bool(macro.get("tushare_macro_news"))
    ts_backfill = bool(macro.get("tushare_macro_backfill") or dq.get("tushare_macro_backfill"))
    ts_perm = _err_has(errors, "没有接口", "权限", "Tushare 未配置", "tushare_unavailable")
    ts_rate = _err_has(errors, "频率超限")
    if ts_news and not ts_backfill and not ts_perm:
        add(
            name="Tushare 宏观/公司增强",
            provider="Tushare Pro",
            fetches="重大新闻、联播、财务指标、业绩预告、估值、解禁等",
            status="ok",
            detail=f"宏观新闻约 {len(macro.get('tushare_macro_news') or [])} 条",
            used_in="双源交叉、盈利修正、公告/解禁风险；补强 §0/§3",
        )
    elif ts_news and ts_backfill:
        add(
            name="Tushare 宏观/公司增强",
            provider="Tushare Pro（宏观新闻由东财/新浪回填）",
            fetches="重大新闻、财务、预告、估值等",
            status="fallback",
            detail="Tushare 新闻接口不可用或未授权，已用替代源回填宏观新闻池",
            used_in="新闻可用；fina/forecast 等仍可能缺，影响盈利修正强度",
        )
    elif ts_perm or ts_rate:
        add(
            name="Tushare 宏观/公司增强",
            provider="Tushare Pro",
            fetches="重大新闻、财务指标、业绩预告、估值、解禁等",
            status="fail",
            detail=(
                "无接口权限或频率超限"
                if ts_perm or ts_rate
                else "不可用"
            )
            + "；积分/权限不足时常见",
            used_in="缺少双源估值与业绩预告时，§3 更依赖 AkShare 财务摘要",
        )
    else:
        add(
            name="Tushare 宏观/公司增强",
            provider="Tushare Pro",
            fetches="重大新闻、财务指标、业绩预告、估值、解禁等",
            status="empty",
            detail="本轮无 Tushare 宏观新闻产出",
            used_in="增强层缺失不影响 AkShare 主链路",
        )

    # —— 个股深度（聚合） ——
    stocks = result.get("stocks") or []
    if stocks:
        add(
            name="个股行情与深度情报",
            provider="东财/新浪日K + 个股新闻/资金流/研报等",
            fetches="K 线、报价、个股新闻与另类数据",
            status="ok",
            detail=f"深度分析 {len(stocks)} 只（来自筛股/必跟）",
            used_in="§3 个股决策链 → §4 终局动作；盈利修正/硬门禁",
        )
    else:
        add(
            name="个股行情与深度情报",
            provider="东财/新浪 + 个股情报",
            fetches="K 线、报价、个股新闻",
            status="empty",
            detail="本轮无深度个股（空仓且筛股未产出时可能发生）",
            used_in="§4 以观察/空仓纪律为主",
        )

    ok_n = sum(1 for r in rows if r["status"] == "ok")
    fb_n = sum(1 for r in rows if r["status"] in ("fallback", "degraded"))
    fail_n = sum(1 for r in rows if r["status"] in ("fail", "empty"))
    skip_n = sum(1 for r in rows if r["status"] == "skipped")

    return {
        "rows": rows,
        "summary": {
            "ok": ok_n,
            "fallback_or_degraded": fb_n,
            "fail_or_empty": fail_n,
            "skipped": skip_n,
            "total": len(rows),
            "dq_score": dq.get("score"),
            "dq_degraded": bool(dq.get("degraded")),
            "dq_note": dq.get("note") or "",
        },
    }


def render_data_sources_section(result: dict[str, Any]) -> list[str]:
    """报告开头的数据源详细说明（Markdown 行）。"""
    ledger = result.get("data_sources") or build_data_sources_ledger(result)
    rows = ledger.get("rows") or []
    summary = ledger.get("summary") or {}
    dq = result.get("data_quality") or {}

    lines: list[str] = []
    lines.append("## 数据源说明（本轮）")
    lines.append("")
    flag = "⚠️ DEGRADED" if (summary.get("dq_degraded") or dq.get("degraded")) else "OK"
    score = summary.get("dq_score", dq.get("score", "-"))
    note = summary.get("dq_note") or dq.get("note") or ""
    lines.append(f"**总评**: {score} ({flag})" + (f" — {note}" if note else ""))
    lines.append(
        f"- 统计: ✅成功 {summary.get('ok', 0)} · "
        f"⚠️降级/备源 {summary.get('fallback_or_degraded', 0)} · "
        f"❌失败/空 {summary.get('fail_or_empty', 0)} · "
        f"➖跳过 {summary.get('skipped', 0)} "
        f"（共 {summary.get('total', len(rows))} 项）"
    )
    if dq.get("missing"):
        lines.append(f"- 质量检查缺失项: {', '.join(dq['missing'])}")
    if dq.get("screen_note"):
        lines.append(f"- 遴选备注: {dq['screen_note']}")
    if dq.get("llm_degraded"):
        lines.append(f"- 决策降级: {dq.get('llm_note') or 'LLM/多Agent 降级'}")
    lines.append("")
    lines.append("| 状态 | 数据源 | 尝试连接 / 提供方 | 获取什么 | 是否拿到 | 后面怎么用 |")
    lines.append("|------|--------|-------------------|----------|----------|------------|")
    for r in rows:
        got = "是" if r.get("obtained") else "否"
        if r.get("status") == "skipped":
            got = "未尝试"
        detail = str(r.get("detail") or "").replace("|", "/")
        fetches = str(r.get("fetches") or "").replace("|", "/")
        used = str(r.get("used_in") or "").replace("|", "/")
        provider = str(r.get("provider") or "").replace("|", "/")
        name = str(r.get("name") or "").replace("|", "/")
        lines.append(
            f"| {r.get('icon', '')} | {name} | {provider} | {fetches} | "
            f"{got}：{detail} | {used} |"
        )
    lines.append("")
    lines.append(
        "_图例：✅ 主源成功并已应用 · ⚠️ 备源/降级仍可用 · ❌ 未获取 · ➖ 本轮未启用。"
        "详细论证中的引用应能在上表找到对应来源。_"
    )
    lines.append("")
    return lines
