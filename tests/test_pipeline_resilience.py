"""容错：LLM 降级继续、异常保留已采数据、失败不覆盖丰满 datasources。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from money_more.analysis.pipeline import DecisionPipeline
from money_more.report.writer import render_daily_report, render_run_status_section, save_report


class _BoomLLM:
    """所有 analyze_json 均失败。"""

    def analyze_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("deepseek Connection error")


class _FakeDB:
    def fail_stuck_runs(self, max_hours: int = 6) -> None:
        return None

    def start_run(self, run_date: Any) -> int:
        return 1

    def finish_run(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_prior_context(self, limit: int = 5) -> list:
        return []

    def get_trend_report(self) -> dict:
        return {}

    def get_active_lessons(self, limit: int = 20) -> list:
        return []

    def save_intelligence_digest(self, *a: Any, **k: Any) -> None:
        return None

    def save_market_snapshot(self, *a: Any, **k: Any) -> None:
        return None

    def save_sector_snapshot(self, *a: Any, **k: Any) -> None:
        return None

    def save_stock_snapshot(self, *a: Any, **k: Any) -> None:
        return None

    def get_sector_analysis_series(self, *a: Any, **k: Any) -> list:
        return []

    def get_stock_analysis_series(self, *a: Any, **k: Any) -> list:
        return []

    def get_recommendations_for_review(self, **k: Any) -> list:
        return []

    def save_recommendation(self, *a: Any, **k: Any) -> None:
        return None

    def save_review(self, *a: Any, **k: Any) -> None:
        return None

    def get_open_paper_trades(self) -> list:
        return []

    def compute_factor_ic_rows(self, *a: Any, **k: Any) -> list:
        return []


class _FakeFetcher:
    def set_as_of(self, d: Any) -> None:
        return None

    def reset_run_cache(self) -> None:
        return None

    def fetch_market_overview(self) -> dict[str, Any]:
        return {
            "indices": [{"name": "上证", "change_pct": 0.5}],
            "limit_up_count": 10,
            "limit_down_count": 5,
        }

    def _get_spot_df(self) -> Any:
        return None

    def fetch_sector_data(self, sector: str) -> dict[str, Any]:
        return {"sector": sector, "change_pct": 1.0}

    def fetch_stock_data(self, code: str) -> dict[str, Any]:
        return {
            "code": code,
            "quote": {"名称": "测试", "最新价": 10.0, "涨跌幅": 1.0},
            "history": {"close": 10.0, "change_pct": 1.0, "volume": 1e6},
        }

    def fetch_current_price(self, code: str) -> float | None:
        return 10.0


class _FakeIntel:
    def set_as_of(self, d: Any) -> None:
        return None

    def reset_run_cache(self) -> None:
        return None

    def fetch_macro_intelligence(self) -> dict[str, Any]:
        return {
            "policy_news": [{"title": "政策A"}],
            "global_news": [{"title": "快讯B"}],
            "margin_trend": {"latest": {"融资余额": 1}},
            "northbound_summary": [{"净买入": 1}],
            "northbound_freshness": {"stale": False, "latest_date": "2026-08-01"},
            "sector_money_flow": {"top_inflow": [{"name": "银行"}], "top_gainers": []},
            "sector_money_flow_source": "ths_summary",
            "sentiment_overview": {"aggregate": {"score_100": 50, "label": "neutral"}},
            "economic_calendar": [{"e": 1}],
            "macro_hard": {"pmi": [{"v": 50}]},
            "global_liquidity": {"stance": "neutral", "source": ["bond"]},
            "rss_telegraph": [{"title": "电报"}],
            "errors": [],
        }

    def fetch_sector_intelligence(self, sector: str) -> dict[str, Any]:
        return {"related_news": []}

    def fetch_stock_intelligence(self, code: str) -> dict[str, Any]:
        return {"news": [], "tushare": {}}


def _minimal_config(tmp_path: Path) -> Any:
    from money_more.config import load_config
    import os

    # Use example if present
    root = Path(__file__).resolve().parents[1]
    cfg_path = root / "config.yaml.example"
    cfg = load_config(str(cfg_path) if cfg_path.exists() else None)
    cfg.project_root = tmp_path
    cfg.intelligence.enabled = True
    cfg.intelligence.digest_before_analysis = True
    cfg.screen.enabled = False  # 避免全市场 spot
    cfg.watch_sectors = ["银行"]
    cfg.holdings = []
    cfg.agents.enabled = False
    cfg.agents.decision_multi = False
    cfg.trend.enabled = False
    cfg.analysis.debate_top_k = 0
    if hasattr(cfg, "sim") and cfg.sim is not None:
        cfg.sim.enabled = False
    return cfg


def test_digest_failure_degrades_but_keeps_macro(tmp_path: Path):
    cfg = _minimal_config(tmp_path)
    pipe = DecisionPipeline(cfg, _FakeDB(), _FakeFetcher(), _BoomLLM(), intelligence=_FakeIntel())
    result = pipe.run_daily(__import__("datetime").date(2026, 8, 2))
    assert result.get("run_status") in ("degraded", "success", "aborted")
    # 宏观已采到
    macro = (result.get("intelligence") or {}).get("macro_raw") or {}
    assert macro.get("policy_news")
    assert result.get("data_quality", {}).get("llm_degraded") is True
    digest = (result.get("intelligence") or {}).get("digest") or {}
    assert digest.get("degraded") or "降级" in str(digest.get("executive_summary") or "")
    # 不应整轮丢失市场占位
    assert (result.get("market") or {}).get("analysis")


def test_run_status_mentions_collected_data_not_all_sources_down():
    result = {
        "run_date": "2026-08-02",
        "partial": True,
        "run_status": "aborted",
        "error": "boom",
        "data_quality": {"llm_degraded": True, "llm_note": "中断"},
        "llm_stage_errors": ["情报digest降级: x"],
        "intelligence": {"macro_raw": {"policy_news": [1]}},
    }
    md = "\n".join(render_run_status_section(result))
    assert "已采集" in md or "不是数据源" in md or "数据源台账" in md
    assert "运行状态" in md
    main = render_daily_report(result)
    assert "未完整完成" in main or "运行状态" in main


def test_preserve_datasources_not_overwritten_by_empty_partial(tmp_path: Path):
    day = "2026-08-02"
    rich = tmp_path / f"{day}-datasources.md"
    rich.write_text(
        "# money_more 数据源说明\n\n**数据完整度**: 0.9 (OK)\n\n"
        "| 状态 | 数据源 |\n|------|--------|\n| ✅ | 北向资金 |\n" + ("x\n" * 50),
        encoding="utf-8",
    )
    (tmp_path / f"{day}.json").write_text(
        '{"run_date":"2026-08-02","intelligence":{"macro_raw":{"policy_news":[{"t":1}],'
        '"northbound_summary":[{"n":1}],"margin_trend":{"latest":1},'
        '"global_news":[{"t":1}],"sentiment_overview":{"aggregate":{"score_100":50}},'
        '"sector_money_flow":{"top_inflow":[]},"global_liquidity":{"stance":"neutral"}}},'
        '"screen":{"enabled":false},"stocks":[]}',
        encoding="utf-8",
    )
    empty = {
        "run_date": day,
        "partial": True,
        "run_status": "aborted",
        "error": "fail",
        "intelligence": {},
        "screen": {},
        "stocks": [],
        "sectors": [],
        "market": {},
        "recommendations": [],
        "data_quality": {"llm_degraded": True, "llm_note": "中断"},
    }
    save_report(empty, tmp_path, preserve_existing_datasources=True)
    text = rich.read_text(encoding="utf-8")
    assert "✅" in text
    assert "北向资金" in text
