"""滚动趋势报告：每日合并新变化，维护跨日叙事与关键指标序列。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from money_more.llm.client import LLMClient
from money_more.storage.db import Database

TREND_UPDATE_SYSTEM = """你是 A 股趋势跟踪分析师。任务是把「今日分析」合并进「既有趋势报告」，输出更新后的完整趋势报告 JSON。

原则：
1. 保留历史序列，不要丢弃旧日期数据
2. 明确标注今日相对昨日的变化（延续 / 转折 / 强化 / 弱化）
3. 区分「短期噪声」与「趋势切换」；仅当证据充分时才宣布 regime 切换
4. 板块与个股只保留用户关注池内的对象
5. 叙事要可追溯：引用日期与关键信号

必须输出 JSON：
{
  "as_of": "YYYY-MM-DD",
  "updated_at": "ISO时间",
  "market_regime": {
    "current_phase": "bull|bear|range",
    "current_label": "中文",
    "current_style": "value|growth|theme",
    "risk_level": "low|medium|high",
    "primary_driver": "...",
    "allocation_hint": "...",
    "regime_change": "none|emerging|confirmed",
    "change_note": "相对前一交易日的变化说明"
  },
  "market_series": [
    {"date":"YYYY-MM-DD","phase":"...","style":"...","risk":"...","sentiment_score":null,"driver":"..."}
  ],
  "sentiment_trend": {
    "latest_score_100": null,
    "direction": "improving|stable|deteriorating|unknown",
    "note": "..."
  },
  "liquidity_trend": {
    "margin": "扩张|收缩|平稳|未知",
    "northbound": "净流入|净流出|中性|未知",
    "note": "..."
  },
  "sector_trends": [
    {
      "sector": "...",
      "status": "strengthening|weakening|stable|rotating_in|rotating_out",
      "policy_wind": "...",
      "prosperity": "...",
      "narrative": "...",
      "series": [{"date":"...","priority":"...","sentiment_score":null,"fund_flow":"..."}]
    }
  ],
  "stock_trends": [
    {
      "code": "...",
      "name": "...",
      "status": "improving|deteriorating|stable|watch",
      "rating_path": ["hold","buy"],
      "thesis": "...",
      "series": [{"date":"...","rating":"...","quality":"...","valuation":"...","sentiment_score":null}]
    }
  ],
  "open_questions": ["仍待验证的问题"],
  "watch_items": ["未来1-2周需盯的事件/指标"],
  "narrative_log": [
    {"date":"YYYY-MM-DD","headline":"当日主线一句话","delta":"相对前一日变化"}
  ],
  "executive_summary": "200字内：当前趋势状态 + 今日新增变化 + 下一步关注点"
}
"""


class TrendReportBuilder:
    def __init__(self, db: Database, llm: LLMClient | None = None) -> None:
        self.db = db
        self.llm = llm

    def update(self, run_date: str, daily_result: dict[str, Any]) -> dict[str, Any]:
        previous = self.db.get_trend_report() or self._empty_report(run_date)
        previous.pop("_meta", None)

        # 先做确定性合并（保证序列完整），再用 LLM 提炼叙事（可选）
        merged = self._deterministic_merge(previous, run_date, daily_result)

        if self.llm is not None:
            try:
                llm_report = self.llm.analyze_json(
                    TREND_UPDATE_SYSTEM,
                    {
                        "previous_trend_report": previous,
                        "today_date": run_date,
                        "today_market": (daily_result.get("market") or {}).get("analysis"),
                        "today_sectors": [
                            {"sector": s.get("sector"), "analysis": s.get("analysis")}
                            for s in (daily_result.get("sectors") or [])
                        ],
                        "today_stocks": [
                            {"code": s.get("code"), "analysis": s.get("analysis")}
                            for s in (daily_result.get("stocks") or [])
                        ],
                        "today_recommendations": daily_result.get("recommendations") or [],
                        "today_intelligence_digest": (daily_result.get("intelligence") or {}).get("digest"),
                        "deterministic_draft": merged,
                    },
                    temperature=0.2,
                    required_keys=["as_of", "market_regime", "executive_summary", "market_series"],
                )
                merged = self._merge_llm_into_deterministic(merged, llm_report, run_date)
            except Exception as exc:
                merged.setdefault("open_questions", []).append(f"趋势LLM更新失败，已保留确定性合并: {exc}")

        merged["as_of"] = run_date
        merged["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.db.save_trend_report(merged, run_date)
        return merged

    def _empty_report(self, run_date: str) -> dict[str, Any]:
        return {
            "as_of": run_date,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "market_regime": {},
            "market_series": [],
            "sentiment_trend": {},
            "liquidity_trend": {},
            "sector_trends": [],
            "stock_trends": [],
            "open_questions": [],
            "watch_items": [],
            "narrative_log": [],
            "executive_summary": "",
        }

    def _deterministic_merge(
        self, previous: dict[str, Any], run_date: str, daily: dict[str, Any]
    ) -> dict[str, Any]:
        market = (daily.get("market") or {}).get("analysis") or {}
        sent = market.get("sentiment_assessment") or {}
        liq = market.get("liquidity_assessment") or {}

        series = list(previous.get("market_series") or [])
        series = [x for x in series if x.get("date") != run_date]
        series.append(
            {
                "date": run_date,
                "phase": market.get("phase"),
                "style": market.get("style"),
                "risk": market.get("risk_level"),
                "sentiment_score": sent.get("quant_score_100"),
                "driver": market.get("primary_driver"),
            }
        )
        series = series[-60:]

        prev_phase = None
        if len(series) >= 2:
            prev_phase = series[-2].get("phase")
        regime_change = "none"
        if prev_phase and market.get("phase") and prev_phase != market.get("phase"):
            regime_change = "emerging"

        sector_map = {s.get("sector"): s for s in (previous.get("sector_trends") or []) if s.get("sector")}
        for sec in daily.get("sectors") or []:
            name = sec.get("sector")
            a = sec.get("analysis") or {}
            s_sent = a.get("sentiment") or {}
            entry = sector_map.get(name) or {
                "sector": name,
                "status": "stable",
                "policy_wind": a.get("policy_wind"),
                "prosperity": a.get("prosperity"),
                "narrative": a.get("narrative"),
                "series": [],
            }
            entry_series = [x for x in (entry.get("series") or []) if x.get("date") != run_date]
            entry_series.append(
                {
                    "date": run_date,
                    "priority": a.get("priority"),
                    "sentiment_score": s_sent.get("quant_score_100"),
                    "fund_flow": a.get("fund_flow_verdict"),
                    "policy_wind": a.get("policy_wind"),
                    "prosperity": a.get("prosperity"),
                }
            )
            entry["series"] = entry_series[-60:]
            entry["policy_wind"] = a.get("policy_wind")
            entry["prosperity"] = a.get("prosperity")
            entry["narrative"] = a.get("narrative")
            entry["status"] = self._sector_status(entry["series"])
            sector_map[name] = entry

        stock_map = {s.get("code"): s for s in (previous.get("stock_trends") or []) if s.get("code")}
        for st in daily.get("stocks") or []:
            code = st.get("code")
            a = st.get("analysis") or {}
            s_sent = a.get("sentiment") or {}
            entry = stock_map.get(code) or {
                "code": code,
                "name": a.get("name"),
                "status": "stable",
                "rating_path": [],
                "thesis": a.get("investment_thesis"),
                "series": [],
            }
            entry_series = [x for x in (entry.get("series") or []) if x.get("date") != run_date]
            entry_series.append(
                {
                    "date": run_date,
                    "rating": a.get("research_rating"),
                    "quality": a.get("quality"),
                    "valuation": a.get("valuation"),
                    "sentiment_score": s_sent.get("quant_score_100"),
                }
            )
            entry["series"] = entry_series[-60:]
            entry["name"] = a.get("name") or entry.get("name")
            entry["thesis"] = a.get("investment_thesis") or entry.get("thesis")
            ratings = [x.get("rating") for x in entry["series"] if x.get("rating")]
            entry["rating_path"] = ratings[-10:]
            entry["status"] = self._stock_status(entry["series"])
            stock_map[code] = entry

        narrative_log = list(previous.get("narrative_log") or [])
        narrative_log = [x for x in narrative_log if x.get("date") != run_date]
        digest = (daily.get("intelligence") or {}).get("digest") or {}
        headline = digest.get("executive_summary") or market.get("summary") or ""
        if isinstance(headline, str) and len(headline) > 80:
            headline = headline[:80] + "…"
        delta = "首日建档" if not previous.get("market_series") else "见 change_note"
        narrative_log.append({"date": run_date, "headline": headline, "delta": delta})
        narrative_log = narrative_log[-90:]

        return {
            "as_of": run_date,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "market_regime": {
                "current_phase": market.get("phase"),
                "current_label": market.get("phase_label"),
                "current_style": market.get("style"),
                "risk_level": market.get("risk_level"),
                "primary_driver": market.get("primary_driver"),
                "allocation_hint": market.get("sector_allocation_hint"),
                "regime_change": regime_change,
                "change_note": self._market_change_note(series, market),
            },
            "market_series": series,
            "sentiment_trend": {
                "latest_score_100": sent.get("quant_score_100"),
                "direction": self._score_direction([x.get("sentiment_score") for x in series]),
                "note": sent.get("narrative") or "",
            },
            "liquidity_trend": {
                "margin": liq.get("margin_trend"),
                "northbound": liq.get("northbound"),
                "note": liq.get("overall") or "",
            },
            "sector_trends": list(sector_map.values()),
            "stock_trends": list(stock_map.values()),
            "open_questions": self._merge_open_questions(
                previous.get("open_questions") or [],
                daily,
                run_date,
            ),
            "watch_items": list((digest.get("macro_events_watchlist") or [])[:5])
            if isinstance(digest.get("macro_events_watchlist"), list)
            else list(previous.get("watch_items") or [])[:8],
            "narrative_log": narrative_log,
            "executive_summary": "",
            "data_quality": daily.get("data_quality") or previous.get("data_quality") or {},
            "paper_stats_hint": "运行 money-more stats 查看纸面胜率",
        }

    @staticmethod
    def _merge_llm_into_deterministic(
        deterministic: dict[str, Any], llm_report: dict[str, Any], run_date: str
    ) -> dict[str, Any]:
        out = dict(deterministic)
        # 保留确定性序列，采用 LLM 的叙事与状态判断
        for key in (
            "market_regime",
            "sentiment_trend",
            "liquidity_trend",
            "open_questions",
            "watch_items",
            "executive_summary",
        ):
            if llm_report.get(key):
                if key == "market_regime" and isinstance(llm_report[key], dict):
                    merged_regime = dict(out.get("market_regime") or {})
                    merged_regime.update(llm_report[key])
                    # 序列相关字段以确定性为准
                    for keep in ("current_phase", "current_style", "risk_level"):
                        if deterministic.get("market_regime", {}).get(keep) is not None:
                            merged_regime[keep] = deterministic["market_regime"][keep]
                    out[key] = merged_regime
                else:
                    out[key] = llm_report[key]

        # 板块/个股：用 LLM status/narrative，保留确定性 series
        if llm_report.get("sector_trends"):
            llm_sec = {s.get("sector"): s for s in llm_report["sector_trends"] if s.get("sector")}
            merged_secs = []
            for s in out.get("sector_trends") or []:
                extra = llm_sec.get(s.get("sector")) or {}
                item = dict(s)
                for k in ("status", "narrative", "policy_wind", "prosperity"):
                    if extra.get(k):
                        item[k] = extra[k]
                merged_secs.append(item)
            out["sector_trends"] = merged_secs

        if llm_report.get("stock_trends"):
            llm_st = {s.get("code"): s for s in llm_report["stock_trends"] if s.get("code")}
            merged_sts = []
            for s in out.get("stock_trends") or []:
                extra = llm_st.get(s.get("code")) or {}
                item = dict(s)
                for k in ("status", "thesis", "name"):
                    if extra.get(k):
                        item[k] = extra[k]
                merged_sts.append(item)
            out["stock_trends"] = merged_sts

        if llm_report.get("open_questions"):
            # 规范化 LLM 输出的问题列表
            out["open_questions"] = TrendReportBuilder._merge_open_questions(
                list(deterministic.get("open_questions") or []) + list(llm_report.get("open_questions") or []),
                {},
                run_date,
            )

        if llm_report.get("narrative_log"):
            # 合并叙事日志，同日以 LLM 为准
            by_date = {x.get("date"): x for x in (out.get("narrative_log") or [])}
            for item in llm_report["narrative_log"]:
                if item.get("date"):
                    by_date[item["date"]] = item
            out["narrative_log"] = [by_date[k] for k in sorted(by_date.keys())][-90:]

        if not out.get("executive_summary"):
            out["executive_summary"] = llm_report.get("executive_summary") or ""
        out["as_of"] = run_date
        return out

    @staticmethod
    def _merge_open_questions(
        previous: list[Any], daily: dict[str, Any], run_date: str
    ) -> list[dict[str, Any]]:
        """结构化待验证问题：opened_on / last_confirmed / expires_on / status。"""
        from datetime import date as date_cls, timedelta

        def _norm(item: Any) -> dict[str, Any] | None:
            if isinstance(item, dict) and item.get("text"):
                return {
                    "text": str(item["text"]).strip(),
                    "opened_on": item.get("opened_on") or run_date,
                    "last_confirmed": item.get("last_confirmed") or item.get("opened_on") or run_date,
                    "expires_on": item.get("expires_on"),
                    "status": item.get("status") or "open",
                }
            if isinstance(item, str) and item.strip():
                return {
                    "text": item.strip(),
                    "opened_on": run_date,
                    "last_confirmed": run_date,
                    "expires_on": None,
                    "status": "open",
                }
            return None

        by_text: dict[str, dict[str, Any]] = {}
        for item in previous:
            n = _norm(item)
            if n:
                by_text[n["text"]] = n

        # 今日风险旗标 / 信息缺口 → 新问题
        digest = (daily.get("intelligence") or {}).get("digest") or {}
        fresh_texts: list[str] = []
        for key in ("risk_flags", "information_gaps"):
            for x in digest.get(key) or []:
                if isinstance(x, str) and x.strip():
                    fresh_texts.append(x.strip())
        for rec in daily.get("recommendations") or []:
            inv = rec.get("invalidation")
            if isinstance(inv, str) and inv.strip():
                fresh_texts.append(f"验证失效条件: {inv.strip()} ({rec.get('code')})")

        for text in fresh_texts:
            if text in by_text:
                by_text[text]["last_confirmed"] = run_date
                if by_text[text].get("status") == "stale":
                    by_text[text]["status"] = "open"
            else:
                try:
                    exp = (date_cls.fromisoformat(run_date) + timedelta(days=14)).isoformat()
                except Exception:
                    exp = None
                by_text[text] = {
                    "text": text,
                    "opened_on": run_date,
                    "last_confirmed": run_date,
                    "expires_on": exp,
                    "status": "open",
                }

        out: list[dict[str, Any]] = []
        for q in by_text.values():
            status = q.get("status") or "open"
            try:
                last = date_cls.fromisoformat(str(q.get("last_confirmed") or run_date)[:10])
                as_of = date_cls.fromisoformat(run_date)
                if (as_of - last).days > 14:
                    status = "stale"
                exp = q.get("expires_on")
                if exp and as_of > date_cls.fromisoformat(str(exp)[:10]):
                    status = "expired"
            except Exception:
                pass
            q["status"] = status
            if status != "expired":
                out.append(q)
        # 活跃优先
        out.sort(key=lambda x: (0 if x.get("status") == "open" else 1, x.get("opened_on") or ""))
        return out[-15:]

    @staticmethod
    def _market_change_note(series: list[dict], market: dict) -> str:
        if len(series) < 2:
            return "趋势报告初始化"
        prev, curr = series[-2], series[-1]
        parts = []
        if prev.get("phase") != curr.get("phase"):
            parts.append(f"阶段 {prev.get('phase')}→{curr.get('phase')}")
        if prev.get("style") != curr.get("style"):
            parts.append(f"风格 {prev.get('style')}→{curr.get('style')}")
        if prev.get("risk") != curr.get("risk"):
            parts.append(f"风险 {prev.get('risk')}→{curr.get('risk')}")
        ps, cs = prev.get("sentiment_score"), curr.get("sentiment_score")
        if isinstance(ps, (int, float)) and isinstance(cs, (int, float)):
            parts.append(f"舆情分 {ps}→{cs}")
        if market.get("primary_driver"):
            parts.append(f"主驱动:{market['primary_driver']}")
        return "；".join(parts) if parts else "关键指标大体延续"

    @staticmethod
    def _score_direction(scores: list[Any]) -> str:
        nums = [s for s in scores if isinstance(s, (int, float))]
        if len(nums) < 2:
            return "unknown"
        delta = nums[-1] - nums[-2]
        if delta >= 5:
            return "improving"
        if delta <= -5:
            return "deteriorating"
        return "stable"

    @staticmethod
    def _sector_status(series: list[dict]) -> str:
        if len(series) < 2:
            return "stable"
        prev, curr = series[-2], series[-1]
        order = {"low": 0, "medium": 1, "high": 2}
        p = order.get(str(prev.get("priority")), 1)
        c = order.get(str(curr.get("priority")), 1)
        if c > p:
            return "strengthening"
        if c < p:
            return "weakening"
        return "stable"

    @staticmethod
    def _stock_status(series: list[dict]) -> str:
        if len(series) < 2:
            return "stable"
        rank = {
            "strong_buy": 5,
            "buy": 4,
            "hold": 3,
            "reduce": 2,
            "sell": 1,
            "avoid": 0,
        }
        prev = rank.get(str(series[-2].get("rating")), 3)
        curr = rank.get(str(series[-1].get("rating")), 3)
        if curr > prev:
            return "improving"
        if curr < prev:
            return "deteriorating"
        return "stable"
