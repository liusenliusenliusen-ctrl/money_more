"""叙事雷达与侧栏合并（无网络）。"""

from __future__ import annotations

from money_more.analysis.narrative_radar import (
    build_narrative_radar,
    merge_contested_narratives,
    merge_policy_market_scenario,
    seed_contested_from_radar,
)


def test_radar_hits_us_debt_and_national_team() -> None:
    macro = {
        "global_news": [
            {"title": "美债收益率再创新高引发流动性担忧"},
            {"title": "市场传国家队护盘后或将逐步出清"},
        ],
        "rss_telegraph": [{"content": "AI泡沫争论再起，英伟达估值受质疑"}],
    }
    radar = build_narrative_radar(macro, None)
    by_id = {t["id"]: t for t in radar["tracks"]}
    assert by_id["us_liquidity_debt"]["signal_strength"] != "none"
    assert by_id["policy_national_team"]["signal_strength"] != "none"
    assert by_id["ai_valuation_bubble"]["signal_strength"] != "none"
    assert radar["policy_market_hypothesis"]["status"] in ("watch", "elevated")
    assert "叙事雷达" in radar["plain_note"] or "命中" in radar["plain_note"]


def test_seed_and_merge_contested() -> None:
    radar = build_narrative_radar(
        {"policy_news": [{"title": "中央汇金增持宽基ETF稳市"}]},
        None,
    )
    seeded = seed_contested_from_radar(radar, limit=3)
    assert seeded
    merged = merge_contested_narratives(
        [{"title": "自定义尾部", "source_type": "hard_data", "probability": "medium", "confirm_signals": ["a"]}],
        radar,
        limit=3,
    )
    assert merged[0]["title"] == "自定义尾部"
    assert len(merged) <= 3


def test_merge_policy_scenario_prefers_llm_status() -> None:
    radar = build_narrative_radar({}, None)
    out = merge_policy_market_scenario(
        {"status": "elevated", "thesis": "LLM 修订假说", "implication": "降仓"},
        radar,
    )
    assert out["status"] == "elevated"
    assert "LLM" in out["thesis"]
    assert out["implication"] == "降仓"
