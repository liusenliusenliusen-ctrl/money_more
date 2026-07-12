from __future__ import annotations

import json
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from money_more.analysis.context_builder import compact_macro_intel, compact_stock_snap
from money_more.analysis.cross_check import apply_hard_gates, cross_check_stock
from money_more.analysis.debate import apply_debate_to_recommendations, run_top_k_debates
from money_more.analysis.decision_validator import enrich_holdings, validate_recommendations
from money_more.analysis.factor_ic import compute_factor_ic_from_db
from money_more.analysis.factor_scorecard import build_stock_scorecard
from money_more.analysis.invalidation import evaluate_invalidation
from money_more.analysis.sector_map import infer_sector
from money_more.analysis.trend import TrendReportBuilder
from money_more.analysis.weight_adapt import weights_from_ic
from money_more.config import AppConfig
from money_more.data.fetcher import MarketDataFetcher, _safe_float, normalize_code, sector_money_flow_present
from money_more.data.intelligence import IntelligenceFetcher
from money_more.llm.client import (
    DECISION_SYSTEM,
    INTELLIGENCE_DIGEST_SYSTEM,
    LLMClient,
    MARKET_SYSTEM,
    REVIEW_SYSTEM,
    SECTOR_SYSTEM,
    STOCK_SYSTEM,
)
from money_more.storage.db import Database
from money_more.utils.logging_util import setup_logging


log = setup_logging()


class DecisionPipeline:
    """情报 → 市场 → 板块 → 个股 → 交易 → 复盘 → 趋势更新。"""

    def __init__(
        self,
        config: AppConfig,
        db: Database,
        fetcher: MarketDataFetcher,
        llm: LLMClient,
        intelligence: IntelligenceFetcher | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.fetcher = fetcher
        self.llm = llm
        self.intelligence = intelligence or IntelligenceFetcher(config)
        self.trend_builder = TrendReportBuilder(db, llm)
        self._orchestrator = None
        if getattr(config, "agents", None) and config.agents.enabled:
            try:
                from money_more.agents import build_orchestrator

                self._orchestrator = build_orchestrator(config)
            except Exception as exc:
                log.warning("multi-agent orchestrator init failed: %s", exc)

    def run_daily(self, run_date: date | None = None) -> dict[str, Any]:
        run_date = run_date or date.today()
        self.db.fail_stuck_runs(max_hours=6)
        self.fetcher.set_as_of(run_date)
        self.fetcher.reset_run_cache()
        if hasattr(self.intelligence, "set_as_of"):
            self.intelligence.set_as_of(run_date)
        self.intelligence.reset_run_cache()

        run_id = self.db.start_run(run_date)
        try:
            result = self._run_daily_body(run_id, run_date)
            return result
        except Exception:
            self.db.finish_run(run_id, "failed")
            raise

    def _run_daily_body(self, run_id: int, run_date: date) -> dict[str, Any]:
        prior_context = self.db.get_prior_context(limit=5)
        existing_trend = self.db.get_trend_report()
        if existing_trend:
            existing_trend.pop("_meta", None)

        result: dict[str, Any] = {
            "run_id": run_id,
            "run_date": run_date.isoformat(),
            "intelligence": {},
            "market": {},
            "sectors": [],
            "stocks": [],
            "recommendations": [],
            "reviews": [],
            "trend": {},
            "prior_context": prior_context,
            "lessons_used": self.db.get_active_lessons(),
            "data_quality": {},
            "validation_overrides": [],
            "factor_scorecards": {},
            "prompt_version": self.config.analysis.prompt_version,
            "investment_horizon": self.config.analysis.investment_horizon,
            "schedule_cadence": self.config.schedule.cadence,
        }

        intel_enabled = self.config.intelligence.enabled
        macro_intel: dict[str, Any] = {}
        intel_digest: dict[str, Any] = {}

        if intel_enabled:
            macro_intel = self.intelligence.fetch_macro_intelligence()
            result["intelligence"]["macro_raw"] = macro_intel
            result["data_quality"] = self._assess_data_quality(macro_intel)

            if self.config.intelligence.digest_before_analysis:
                intel_digest = self.llm.analyze_json(
                    INTELLIGENCE_DIGEST_SYSTEM,
                    {
                        "date": run_date.isoformat(),
                        "macro_intelligence": compact_macro_intel(macro_intel),
                        "past_lessons": result["lessons_used"],
                        "prior_context": prior_context,
                        "data_quality": result["data_quality"],
                    },
                    required_keys=["executive_summary", "sentiment_temperature"],
                )
                result["intelligence"]["digest"] = intel_digest
                self.db.save_intelligence_digest(run_id, intel_digest)

        market_snapshot = self.fetcher.fetch_market_overview()
        if intel_enabled:
            market_snapshot["intelligence"] = macro_intel

        market_analysis = self.llm.analyze_json(
            MARKET_SYSTEM,
            {
                "date": run_date.isoformat(),
                "market_data": {
                    **{k: v for k, v in market_snapshot.items() if k != "intelligence"},
                    "intelligence": compact_macro_intel(macro_intel) if intel_enabled else {},
                },
                "intelligence_digest": intel_digest,
                "past_lessons": result["lessons_used"],
                "prior_context": prior_context,
                "trend_report_summary": self._trend_summary_for_llm(existing_trend),
                "data_quality": result["data_quality"],
            },
            required_keys=["phase", "style", "risk_level", "summary", "confidence", "vs_prior"],
        )
        self.db.save_market_snapshot(run_id, market_snapshot, market_analysis)
        result["market"] = {"snapshot": market_snapshot, "analysis": market_analysis}

        sector_analyses: list[dict[str, Any]] = []
        for sector in self.config.watch_sectors:
            snap = self.fetcher.fetch_sector_data(sector)
            sector_intel: dict[str, Any] = {}
            if intel_enabled:
                sector_intel = self.intelligence.fetch_sector_intelligence(sector)
                snap["intelligence"] = sector_intel

            analysis = self.llm.analyze_json(
                SECTOR_SYSTEM,
                {
                    "date": run_date.isoformat(),
                    "sector_data": snap,
                    "sector_intelligence": sector_intel,
                    "intelligence_digest": intel_digest,
                    "market_context": market_analysis,
                    "past_lessons": result["lessons_used"],
                    "prior_sector_series": self.db.get_sector_analysis_series(sector, limit=5),
                },
                required_keys=["sector", "worth_research", "summary", "confidence"],
            )
            self.db.save_sector_snapshot(run_id, sector, snap, analysis)
            sector_analyses.append(
                {"sector": sector, "snapshot": snap, "intelligence": sector_intel, "analysis": analysis}
            )
        result["sectors"] = sector_analyses

        stock_codes = list(dict.fromkeys(self.config.watch_stocks + [h.code for h in self.config.holdings]))
        stock_analyses: list[dict[str, Any]] = []
        quotes: dict[str, float | None] = {}
        quotes_meta: dict[str, dict[str, Any]] = {}
        scorecards: dict[str, Any] = {}
        ic_report = compute_factor_ic_from_db(self.db)
        adapted_weights = weights_from_ic(ic_report)
        result["factor_ic"] = ic_report
        result["factor_weights_adapted"] = adapted_weights
        log.info("run_id=%s adapted_weights=%s", run_id, adapted_weights)

        # 并行拉取行情/情报，缩短墙钟时间（LLM 仍串行以保证上下文一致）
        prefetched: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

        def _fetch_one(code: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
            snap = self.fetcher.fetch_stock_data(code)
            intel: dict[str, Any] = {}
            if intel_enabled:
                intel = self.intelligence.fetch_stock_intelligence(code)
                snap["intelligence"] = intel
            return code, snap, intel

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(stock_codes)))) as pool:
            futs = [pool.submit(_fetch_one, c) for c in stock_codes]
            for fut in as_completed(futs):
                code, snap, intel = fut.result()
                prefetched[code] = (snap, intel)

        for code in stock_codes:
            snap, stock_intel = prefetched.get(code, ({}, {}))
            if not snap:
                snap = self.fetcher.fetch_stock_data(code)
                stock_intel = {}
                if intel_enabled:
                    stock_intel = self.intelligence.fetch_stock_intelligence(code)
                    snap["intelligence"] = stock_intel

            ts_bundle = (stock_intel.get("tushare") or {}) if stock_intel else {}
            xcheck = cross_check_stock(snap, ts_bundle)
            gates = apply_hard_gates(code, snap, ts_bundle)
            snap["cross_check"] = xcheck
            snap["hard_gates"] = gates

            analysis = self.llm.analyze_json(
                STOCK_SYSTEM,
                {
                    "date": run_date.isoformat(),
                    "stock_data": compact_stock_snap(snap),
                    "stock_intelligence": compact_stock_snap(snap).get("intelligence"),
                    "cross_check": xcheck,
                    "hard_gates": gates,
                    "intelligence_digest": intel_digest,
                    "market_context": market_analysis,
                    "sector_context": [
                        {"sector": s.get("sector"), "analysis": s.get("analysis")} for s in sector_analyses
                    ],
                    "past_lessons": result["lessons_used"],
                    "prior_stock_series": self.db.get_stock_analysis_series(code, limit=5),
                },
                required_keys=["code", "research_rating", "summary", "confidence"],
            )
            # 双源不一致 → 下调 LLM 置信度
            try:
                conf = float(analysis.get("confidence") or 0.5)
                conf = max(0.05, conf - float(xcheck.get("confidence_haircut") or 0))
                analysis["confidence"] = round(conf, 3)
            except (TypeError, ValueError):
                pass

            scorecard = build_stock_scorecard(snap, analysis, stock_intel, weights=adapted_weights)
            analysis["factor_scorecard"] = scorecard
            analysis["cross_check"] = xcheck
            analysis["hard_gates"] = gates
            scorecards[code] = scorecard

            px = _safe_float((snap.get("history") or {}).get("close"))
            if px is None:
                px = _safe_float((snap.get("quote") or {}).get("最新价"))
            quotes[code] = px
            quotes_meta[code] = {
                "atr_pct_20d": (snap.get("history") or {}).get("atr_pct_20d"),
            }

            self.db.save_stock_snapshot(run_id, code, snap, analysis)
            stock_analyses.append(
                {
                    "code": code,
                    "snapshot": snap,
                    "intelligence": stock_intel,
                    "analysis": analysis,
                    "factor_scorecard": scorecard,
                    "cross_check": xcheck,
                    "hard_gates": gates,
                }
            )
        result["stocks"] = stock_analyses
        result["factor_scorecards"] = scorecards

        holdings_enriched = enrich_holdings(self.config.holdings, quotes)
        trading_constraints = {
            "max_single_position_pct": self.config.trading.max_single_position_pct,
            "max_total_position_pct": self.config.trading.max_total_position_pct,
            "stop_loss_pct": self.config.trading.stop_loss_pct,
            "take_profit_pct": self.config.trading.take_profit_pct,
        }

        decision_payload = {
            "date": run_date.isoformat(),
            "intelligence_digest": intel_digest,
            "market_analysis": market_analysis,
            "sector_analyses": [s["analysis"] for s in sector_analyses],
            "stock_analyses": stock_analyses,
            "factor_scorecards": scorecards,
            "hard_gates": {s["code"]: s.get("hard_gates") or {} for s in stock_analyses},
            "cross_checks": {s["code"]: s.get("cross_check") or {} for s in stock_analyses},
            "holdings": holdings_enriched,
            "trading_constraints": trading_constraints,
            "investment_horizon": self.config.analysis.investment_horizon,
            "default_time_horizon": self.config.analysis.default_time_horizon,
            "schedule_cadence": self.config.schedule.cadence,
            "past_lessons": result["lessons_used"],
            "prior_context": prior_context,
            "trend_report_summary": self._trend_summary_for_llm(existing_trend),
            "data_quality": result["data_quality"],
        }
        use_multi = bool(
            self._orchestrator
            and self.config.agents.enabled
            and self.config.agents.decision_multi
        )
        if use_multi:
            log.info(
                "decision via multi-agent primary=%s secondary=%s synth=%s",
                self._orchestrator.primary.name,
                self._orchestrator.secondary.name if self._orchestrator.secondary else None,
                self._orchestrator.synthesizer.name if self._orchestrator.synthesizer else None,
            )
            decision = self._orchestrator.analyze_json(
                DECISION_SYSTEM,
                decision_payload,
                required_keys=["recommendations", "portfolio_summary"],
                multi=True,
            )
        else:
            decision = self.llm.analyze_json(
                DECISION_SYSTEM,
                decision_payload,
                required_keys=["recommendations", "portfolio_summary"],
            )
        result["multi_agent"] = {
            "enabled": use_multi,
            "meta": decision.get("_multi_agent") or decision.get("_multi_agent_fallback"),
            "errors": decision.get("_multi_agent_errors") or [],
        }
        # 草稿较大，只保留摘要键，避免报告爆炸
        drafts = decision.pop("_analyst_drafts", None)
        if drafts:
            result["multi_agent"]["draft_agents"] = list(drafts.keys())
            result["multi_agent_drafts"] = drafts

        raw_recs = decision.get("recommendations") or []
        # Top-K 多空辩论（TradingAgents 轻量版）
        debates: dict[str, Any] = {}
        debate_overrides: list[str] = []
        if self.config.analysis.debate_top_k > 0:
            debates = run_top_k_debates(
                self.llm,
                stock_analyses,
                top_k=self.config.analysis.debate_top_k,
                min_score=self.config.analysis.debate_min_score,
            )
            debate_overrides = apply_debate_to_recommendations(raw_recs, debates)
        result["debates"] = debates

        # 硬门禁：ST/涨跌停等强制 watch
        gate_map = {s["code"]: s.get("hard_gates") or {} for s in stock_analyses}
        snap_map = {s["code"]: s.get("snapshot") or {} for s in stock_analyses}
        for rec in raw_recs:
            code = normalize_code(str(rec.get("code", "")))
            # 从个股分析/板块上下文推断 sector_tag，供集中度约束
            if not rec.get("sector_tag"):
                rec["sector_tag"] = infer_sector(code, self.config.watch_sectors)
                if not rec.get("sector_tag"):
                    for s in stock_analyses:
                        if s.get("code") == code:
                            summary = str((s.get("analysis") or {}).get("summary") or "")
                            for sec in self.config.watch_sectors:
                                if sec and sec in summary:
                                    rec["sector_tag"] = sec
                                    break
                            break
            g = gate_map.get(code) or {}
            if g.get("force_watch") or g.get("block_buy"):
                if str(rec.get("action", "")).lower() in ("buy", "add"):
                    rec["action"] = "watch"
                    rec["position_pct"] = 0
                    rec.setdefault("rationale", "")
                    rec["rationale"] = (
                        str(rec.get("rationale") or "")
                        + " | 硬门禁: "
                        + "; ".join(g.get("reasons") or [])
                    ).strip(" |")
            inv = evaluate_invalidation(rec.get("invalidation"), snap_map.get(code))
            rec["invalidation_check"] = inv
            if inv.get("invalidated") and str(rec.get("action", "")).lower() in ("buy", "add", "hold"):
                rec["action"] = "watch"
                rec["position_pct"] = 0
                rec["rationale"] = (
                    str(rec.get("rationale") or "")
                    + " | 失效条件已触发: "
                    + "; ".join(inv.get("fired") or [])
                ).strip(" |")
        validated, overrides = validate_recommendations(
            raw_recs,
            holdings=holdings_enriched,
            constraints=trading_constraints,
            quotes=quotes,
            data_quality=result["data_quality"],
            market_risk_level=str(market_analysis.get("risk_level") or ""),
            hard_gates=gate_map,
            quotes_meta=quotes_meta,
        )
        overrides = debate_overrides + overrides
        result["validation_overrides"] = overrides
        from money_more.analysis.risk_check import risk_check_book

        result["risk_check"] = risk_check_book(
            validated,
            max_single=self.config.trading.max_single_position_pct,
            max_total=self.config.trading.max_total_position_pct,
        )
        log.info("run_id=%s debates=%s overrides=%s risk=%s", run_id, list(debates.keys()), len(overrides), result["risk_check"].get("ok"))

        recommendations: list[dict[str, Any]] = []
        for rec in validated:
            # 中长线：强制 time_horizon 不为 short
            th = str(rec.get("time_horizon") or self.config.analysis.default_time_horizon).lower()
            if th == "short" and self.config.analysis.investment_horizon == "medium_long":
                th = self.config.analysis.default_time_horizon
                overrides.append(f"{rec.get('code')}: short→{th}（中长线模式）")
            rec["time_horizon"] = th
            code = normalize_code(str(rec.get("code", "")))
            sc = scorecards.get(code) or {}
            extra = {
                "evidence_chain": rec.get("evidence_chain"),
                "key_risk": rec.get("key_risk"),
                "invalidation": rec.get("invalidation"),
                "invalidation_check": rec.get("invalidation_check"),
                "time_horizon": rec.get("time_horizon"),
                "validation": rec.get("validation"),
                "factor_scorecard": sc,
                "debate": rec.get("debate") or debates.get(code),
                "entry_price": quotes.get(code),
                "cross_check": (snap_map.get(code) or {}).get("cross_check"),
                "hard_gates": gate_map.get(code),
            }
            rec_id = self.db.save_recommendation(
                run_id=run_id,
                stock_code=code,
                action=str(rec.get("action", "watch")),
                confidence=float(rec["confidence"]) if rec.get("confidence") is not None else None,
                target_price=float(rec["target_price"]) if rec.get("target_price") is not None else None,
                stop_loss=float(rec["stop_loss"]) if rec.get("stop_loss") is not None else None,
                position_pct=float(rec["position_pct"]) if rec.get("position_pct") is not None else None,
                rationale=str(rec.get("rationale", "")),
                extra=extra,
            )
            # 纸面交易台账
            action = str(rec.get("action", "watch")).lower()
            if action in ("buy", "add") and quotes.get(code):
                self.db.open_paper_trade(
                    recommendation_id=rec_id,
                    stock_code=code,
                    action=action,
                    entry_date=run_date.isoformat(),
                    entry_price=float(quotes[code]),  # type: ignore[arg-type]
                    stop_loss=float(rec["stop_loss"]) if rec.get("stop_loss") is not None else None,
                    target_price=float(rec["target_price"]) if rec.get("target_price") is not None else None,
                    position_pct=float(rec["position_pct"]) if rec.get("position_pct") is not None else None,
                )
            rec["id"] = rec_id
            rec["factor_scorecard"] = sc
            recommendations.append(rec)
        result["validation_overrides"] = overrides
        result["recommendations"] = recommendations
        result["decision_summary"] = {
            "portfolio_summary": decision.get("portfolio_summary"),
            "market_context": decision.get("market_context"),
            "sentiment_regime_note": decision.get("sentiment_regime_note"),
            "factor_weights_used": decision.get("factor_weights_used"),
            "contradictions_handled": decision.get("contradictions_handled"),
            "validation_overrides": overrides,
            "holdings_enriched": holdings_enriched,
        }

        review_result = self.run_review(
            run_id,
            run_date,
            current_view={
                "market": market_analysis,
                "sectors": sector_analyses,
                "intelligence_digest": intel_digest,
                "recommendations": recommendations,
            },
        )
        result["reviews"] = review_result.get("reviews", [])
        result["dimension_reviews"] = review_result.get("dimension_reviews", [])
        result["history_patterns"] = review_result.get("history_patterns", [])
        result["meta_lessons"] = review_result.get("meta_lessons", [])
        result["sentiment_lessons"] = review_result.get("sentiment_lessons", [])

        # 更新纸面持仓盯市
        self._mark_paper_trades(run_date)

        if self.config.trend.enabled:
            trend_report = self.trend_builder.update(run_date.isoformat(), result)
            result["trend"] = trend_report

        from money_more.analysis.decision_digest import build_decision_digest

        result["decision_digest"] = build_decision_digest(result)
        return result

    def run_review(
        self,
        run_id: int,
        run_date: date,
        current_view: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pending = self.db.get_recommendations_for_review(
            before_date=run_date,
            lookback_days=self.config.review_lookback_days,
        )
        # 至少持有/观察 N 个自然日再评判（中长线默认 14）
        min_hold = int(getattr(self.config.analysis, "review_min_hold_days", 14))
        filtered = []
        for item in pending:
            try:
                rec_d = date.fromisoformat(str(item["run_date"])[:10])
                if (run_date - rec_d).days < min_hold:
                    continue
            except Exception:
                pass
            filtered.append(item)
        pending = filtered

        lookback = int(self.config.review_lookback_days)
        from money_more.analysis.review_history import (
            build_prior_dimension_forecasts,
            compact_current_view,
            load_db_market_history,
            load_historical_reports_corpus,
        )

        reports_dir = self.config.resolve(self.config.paths.reports)
        prior_dims = build_prior_dimension_forecasts(
            reports_dir,
            as_of=run_date,
            lookback_days=lookback,
            min_age_days=min_hold,
            max_items=8,
        )
        current_compact = compact_current_view(current_view)

        if not pending and not prior_dims:
            return {
                "reviews": [],
                "dimension_reviews": [],
                "history_patterns": [],
                "meta_lessons": [],
                "sentiment_lessons": [],
            }

        enriched: list[dict[str, Any]] = []
        for item in pending:
            rec_date = item["run_date"]
            code = item["stock_code"]
            entry_price = self.fetcher.fetch_price_on_date(code, rec_date)
            current_price = self.fetcher.fetch_current_price(code)
            return_pct = None
            if entry_price and current_price:
                return_pct = round((current_price - entry_price) / entry_price * 100, 2)

            original = self.db.get_analysis_at_date(str(rec_date)[:10], str(code))
            report_excerpt = self._load_report_excerpt(str(rec_date)[:10], str(code))
            extra = item.get("extra_json")
            if isinstance(extra, str) and extra.strip():
                try:
                    extra = json.loads(extra)
                except Exception:
                    extra = {"raw": extra[:500]}
            elif not isinstance(extra, dict):
                extra = {}

            enriched.append(
                {
                    "id": item["id"],
                    "run_date": rec_date,
                    "stock_code": code,
                    "action": item.get("action"),
                    "confidence": item.get("confidence"),
                    "target_price": item.get("target_price"),
                    "stop_loss": item.get("stop_loss"),
                    "position_pct": item.get("position_pct"),
                    "rationale": item.get("rationale"),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "return_pct": return_pct,
                    "original_context": {
                        "db_analysis": original,
                        "recommendation_extra": {
                            "evidence_chain": extra.get("evidence_chain"),
                            "key_risk": extra.get("key_risk"),
                            "invalidation": extra.get("invalidation"),
                            "time_horizon": extra.get("time_horizon"),
                            "factor_scorecard": extra.get("factor_scorecard"),
                        },
                        "report_excerpt": report_excerpt,
                    },
                }
            )

        existing_trend = self.db.get_trend_report()
        if existing_trend:
            existing_trend.pop("_meta", None)

        historical_reports = load_historical_reports_corpus(
            reports_dir,
            as_of=run_date,
            lookback_days=lookback,
            max_reports=24,
        )
        historical_reports["db_market_spine"] = load_db_market_history(
            self.db, lookback_days=lookback, limit=30
        )

        review_payload = self.llm.analyze_json(
            REVIEW_SYSTEM,
            {
                "date": run_date.isoformat(),
                "pending_recommendations": enriched,
                "prior_dimension_forecasts": prior_dims,
                "current_view": current_compact,
                "past_lessons": self.db.get_active_lessons(limit=30),
                "prior_context": self.db.get_prior_context(limit=min(20, max(5, lookback // 7))),
                "historical_reports": historical_reports,
                "trend_report_summary": self._trend_summary_for_llm(existing_trend),
                "instruction": (
                    "1) 用 prior_dimension_forecasts 对照 current_view，复盘市场阶段/板块优先级/主叙事/维度联动；"
                    "2) 若有 pending_recommendations，用 original_context 对照个股 thesis；"
                    "3) 正确则写清有效信号，错误则归因；不要只根据 return_pct 下结论。"
                ),
            },
            required_keys=["dimension_reviews"],
        )

        saved_reviews: list[dict[str, Any]] = []
        review_map = {
            int(r["recommendation_id"]): r
            for r in review_payload.get("reviews") or []
            if r.get("recommendation_id")
        }

        for item in enriched:
            rid = int(item["id"])
            rv = review_map.get(rid)
            if not rv:
                # 无匹配 recommendation_id：不写假复盘，保持 pending
                continue
            diagnosis = str(rv.get("diagnosis") or "待进一步观察")
            lesson = str(rv.get("lesson") or "")
            outcome = str(rv.get("outcome") or "pending")
            # 收益以代码计算为准，不信任 LLM 覆盖
            return_pct = item.get("return_pct")
            extra = {
                "what_worked": rv.get("what_worked"),
                "what_failed": rv.get("what_failed"),
                "prompt_adjustment": rv.get("prompt_adjustment"),
                "entry_price": item.get("entry_price"),
                "current_price": item.get("current_price"),
            }

            review_id = self.db.save_review(
                run_id=run_id,
                recommendation_id=rid,
                stock_code=item["stock_code"],
                original_action=item["action"],
                outcome=outcome,
                return_pct=float(return_pct) if return_pct is not None else None,
                diagnosis=diagnosis,
                lesson=lesson,
                diagnosis_category=str(rv.get("diagnosis_category") or "") or None,
                extra=extra,
            )
            saved_reviews.append(
                {
                    "review_id": review_id,
                    "recommendation_id": rid,
                    "stock_code": item["stock_code"],
                    "outcome": outcome,
                    "return_pct": return_pct,
                    "diagnosis": diagnosis,
                    "diagnosis_category": rv.get("diagnosis_category"),
                    "lesson": lesson,
                    "prompt_adjustment": rv.get("prompt_adjustment"),
                }
            )

        dimension_reviews = [
            r for r in (review_payload.get("dimension_reviews") or []) if isinstance(r, dict)
        ]
        # 维度教训入库（按 dimension 分类）
        for dr in dimension_reviews:
            lesson = str(dr.get("lesson") or "").strip()
            if not lesson:
                continue
            dim = str(dr.get("dimension") or "meta").strip() or "meta"
            self.db.insert_lesson_if_new(category=f"dim:{dim}"[:32], content=lesson, lookback_days=14)

        self._insert_unique_lessons(review_payload.get("meta_lessons") or [], "meta")
        self._insert_unique_lessons(review_payload.get("sentiment_lessons") or [], "sentiment")
        self._insert_unique_lessons(review_payload.get("history_patterns") or [], "pattern")

        return {
            "reviews": saved_reviews,
            "dimension_reviews": dimension_reviews,
            "history_patterns": review_payload.get("history_patterns") or [],
            "meta_lessons": review_payload.get("meta_lessons") or [],
            "sentiment_lessons": review_payload.get("sentiment_lessons") or [],
        }

    def _insert_unique_lessons(self, lessons: list[Any], category: str) -> None:
        for lesson in lessons:
            if not lesson or not isinstance(lesson, str):
                continue
            text = lesson.strip()
            if not text:
                continue
            self.db.insert_lesson_if_new(category=category, content=text, lookback_days=7)

    def _load_report_excerpt(self, run_date: str, stock_code: str, max_chars: int = 1800) -> dict[str, Any]:
        """从 reports/YYYY-MM-DD.md 抽取与该股相关的段落，供复盘对照。"""
        from pathlib import Path

        code = normalize_code(stock_code)
        reports_dir = self.config.resolve(self.config.paths.reports)
        path = Path(reports_dir) / f"{run_date}.md"
        out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
        if not path.exists():
            return out
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            out["error"] = str(exc)
            return out

        # 头部：数据质量 + 情报综述前几行
        head_lines = text.splitlines()[:40]
        head = "\n".join(head_lines)[:800]

        # 含股票代码的段落
        chunks: list[str] = []
        buf: list[str] = []
        for line in text.splitlines():
            if line.startswith("## ") or line.startswith("### "):
                block = "\n".join(buf).strip()
                if block and code in block:
                    chunks.append(block[:600])
                buf = [line]
            else:
                buf.append(line)
        block = "\n".join(buf).strip()
        if block and code in block:
            chunks.append(block[:600])

        body = "\n\n---\n\n".join(chunks[:3])
        combined = (head + "\n\n" + body).strip()
        out["excerpt"] = combined[:max_chars]
        out["matched_sections"] = len(chunks)
        return out

    def _mark_paper_trades(self, run_date: date) -> None:
        open_trades = self.db.get_open_paper_trades()
        for trade in open_trades:
            code = trade["stock_code"]
            px = self.fetcher.fetch_current_price(code)
            if px is None:
                continue
            entry = float(trade["entry_price"])
            ret = round((px - entry) / entry * 100, 2) if entry else None
            if ret is not None:
                from money_more.analysis.costs import apply_ashare_costs

                ret = apply_ashare_costs(ret, side="roundtrip")
            stop = trade.get("stop_loss")
            target = trade.get("target_price")
            status = "open"
            exit_reason = None
            if stop is not None and px <= float(stop):
                status = "closed"
                exit_reason = "stop_loss"
            elif target is not None and px >= float(target):
                status = "closed"
                exit_reason = "take_profit"
            else:
                try:
                    ed = date.fromisoformat(str(trade["entry_date"])[:10])
                    horizon = int(getattr(self.config.analysis, "paper_horizon_days", 60))
                    if (run_date - ed).days >= horizon:
                        status = "closed"
                        exit_reason = f"horizon_{horizon}d"
                except Exception:
                    pass
            self.db.update_paper_trade(
                trade_id=int(trade["id"]),
                current_price=px,
                return_pct=ret,
                status=status,
                exit_date=run_date.isoformat() if status == "closed" else None,
                exit_price=px if status == "closed" else None,
                exit_reason=exit_reason,
                max_dd_pct=self._update_max_dd(trade.get("max_dd_pct"), ret),
            )

    @staticmethod
    def _update_max_dd(prev: Any, ret: float | None) -> float | None:
        if ret is None:
            return float(prev) if prev is not None else None
        prev_f = float(prev) if prev is not None else 0.0
        return min(prev_f, ret) if ret < 0 else prev_f

    @staticmethod
    def _assess_data_quality(macro_intel: dict[str, Any]) -> dict[str, Any]:
        errors = list(macro_intel.get("errors") or [])
        err_text = " ".join(errors).lower()
        tushare_bad = any(
            x in err_text
            for x in (
                "token",
                "权限",
                "积分",
                "tushare 未配置",
                "tushare_unavailable",
                "认证",
                "频率超限",
                "鉴权",
            )
        )
        has_macro_news = bool(macro_intel.get("tushare_macro_news"))
        checks = {
            "policy_news": bool(macro_intel.get("policy_news")),
            "global_news": bool(macro_intel.get("global_news") or macro_intel.get("global_news_sina")),
            "rss_or_flash": bool(macro_intel.get("rss_telegraph") or macro_intel.get("rss_important")),
            "margin_trend": bool(macro_intel.get("margin_trend")),
            "northbound": bool(macro_intel.get("northbound_summary"))
            and (macro_intel.get("northbound_freshness") or {}).get("stale") is not True,
            "sentiment_overview": bool((macro_intel.get("sentiment_overview") or {}).get("aggregate")),
            "economic_calendar": bool(
                macro_intel.get("economic_calendar")
                or macro_intel.get("economic_calendar_alt")
                or macro_intel.get("economic_calendar_synthetic")
            ),
            "tushare_macro": has_macro_news,
            "sector_money_flow": sector_money_flow_present(macro_intel.get("sector_money_flow")),
            "macro_hard": bool(macro_intel.get("macro_hard")),
        }
        missing = [k for k, ok in checks.items() if not ok]
        if "policy_news_stale_or_empty" in errors:
            missing.append("policy_news_fresh")
        score = round(sum(1 for ok in checks.values() if ok) / max(len(checks), 1), 2)
        # Tushare 不可用且替代源也无法补宏观新闻时才扣分
        if tushare_bad and not has_macro_news:
            score = round(max(0.0, score - 0.15), 2)
            missing.append("tushare_available")
        degraded = score < 0.6
        return {
            "score": score,
            "checks": checks,
            "missing": missing,
            "error_count": len(errors),
            "errors_sample": errors[:8],
            "degraded": degraded,
            "tushare_macro_backfill": bool(macro_intel.get("tushare_macro_backfill")),
            "note": "DEGRADED：数据完整度偏低，已收紧仓位/禁止激进开仓" if degraded else "数据完整度尚可",
        }

    @staticmethod
    def _trend_summary_for_llm(trend: dict[str, Any] | None) -> dict[str, Any]:
        if not trend:
            return {}
        return {
            "as_of": trend.get("as_of"),
            "market_regime": trend.get("market_regime"),
            "sentiment_trend": trend.get("sentiment_trend"),
            "liquidity_trend": trend.get("liquidity_trend"),
            "executive_summary": trend.get("executive_summary"),
            "recent_narrative": (trend.get("narrative_log") or [])[-5:],
            "open_questions": (trend.get("open_questions") or [])[:5],
        }
