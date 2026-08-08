#!/usr/bin/env python3
"""用历史报告重建建议段 payload，复测 primary complete_json（验证 max_tokens）。

示例：
  .venv/bin/python scripts/replay_advice_max_tokens.py \\
      --report reports/2026-08-08.json --max-tokens 32768
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def rebuild_decision_payload(report: dict) -> dict:
    intel = report.get("intelligence") or {}
    market = (report.get("market") or {}).get("analysis") or {}
    screen = report.get("screen") or {}
    summary = report.get("decision_summary") or {}
    return {
        "module": "advice",
        "date": report.get("run_date"),
        "research_book": report.get("research_book") or {},
        "intelligence_digest": intel.get("digest") or {},
        "contested_narratives": market.get("contested_narratives") or [],
        "policy_market_scenario": market.get("policy_market_scenario") or {},
        "narrative_radar": intel.get("narrative_radar") or {},
        "market_microstructure": report.get("market_microstructure") or {},
        "global_liquidity": intel.get("global_liquidity") or {},
        "equity_bond": report.get("equity_bond") or {},
        "holdings": report.get("holdings") or [],
        "holdings_basis": summary.get("holdings_basis") or {},
        "screen_summary": {
            "note": screen.get("note"),
            "deep_codes": screen.get("deep_codes"),
            "force_codes": screen.get("force_codes"),
            "top_candidates": (screen.get("top_candidates") or [])[:12],
        },
        "trading_constraints": {
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        "investment_horizon": report.get("investment_horizon"),
        "default_time_horizon": "medium",
        "schedule_cadence": report.get("schedule_cadence"),
        "past_lessons": report.get("lessons_used") or [],
        "prior_context": report.get("prior_context") or {},
        "trend_report_summary": {},
        "data_quality": report.get("data_quality") or {},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="reports/2026-08-08.json")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--max-tokens", type=int, default=None, help="覆盖 agents.llm_max_tokens")
    ap.add_argument("--max-retries", type=int, default=0, help="复测默认不重试，一次看清 finish")
    args = ap.parse_args()

    from money_more.config import load_config
    from money_more.llm.client import ADVICE_SYSTEM
    from money_more.llm.providers.openai_compat import OpenAICompatProvider
    from money_more.utils.json_util import dumps_json

    cfg = load_config(args.config)
    max_tokens = int(
        args.max_tokens
        if args.max_tokens is not None
        else getattr(cfg.agents, "llm_max_tokens", 32768)
    )
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    payload = rebuild_decision_payload(report)
    payload_chars = len(dumps_json(payload, indent=2))

    provider = OpenAICompatProvider(
        name="deepseek",
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
        model=cfg.llm_model,
        timeout=float(cfg.agents.llm_timeout_seconds or 300),
        max_retries=int(args.max_retries),
        max_tokens=max_tokens,
    )
    print(
        f"report={args.report} model={cfg.llm_model} max_tokens={max_tokens} "
        f"payload_chars={payload_chars} retries={args.max_retries}"
    )
    t0 = time.monotonic()
    try:
        data = provider.complete_json(
            ADVICE_SYSTEM,
            payload,
            temperature=0.3,
            required_keys=["recommendations", "portfolio_summary"],
            max_retries=int(args.max_retries),
        )
        elapsed = time.monotonic() - t0
        recs = data.get("recommendations") or []
        ps = data.get("portfolio_summary")
        ps_info = (
            f"keys={list(ps.keys())[:8]}"
            if isinstance(ps, dict)
            else f"type={type(ps).__name__} preview={str(ps)[:80]!r}"
        )
        print(f"OK elapsed={elapsed:.1f}s recs={len(recs)} portfolio_summary {ps_info}")
        out = Path("logs") / f"replay_advice_{Path(args.report).stem}_mt{max_tokens}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dumps_json(data, indent=2), encoding="utf-8")
        print(f"wrote {out}")
        return 0
    except Exception as exc:
        elapsed = time.monotonic() - t0
        print(f"FAIL elapsed={elapsed:.1f}s max_tokens={max_tokens}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
