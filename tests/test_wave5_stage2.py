"""第五波阶段二：证据链 / 矛盾分支 / 验证命中率 / 截断统计。"""

from __future__ import annotations

from datetime import date
from typing import Any

from money_more.analysis.framework_gates import build_contradiction_branches
from money_more.analysis.verify_tracker import evaluate_verify_window
from money_more.llm.providers.openai_compat import OpenAICompatProvider


def test_contradiction_branches_hard_facts_first() -> None:
    branches = build_contradiction_branches(
        True, ["PMI收缩(49.2)", "融资余额近窗收缩(-2.1%)"], ["叙事：政策底已现"]
    )
    assert len(branches) >= 2
    assert branches[0]["topic"] == "景气（PMI）"
    assert branches[0]["if_improves"]
    assert branches[0]["if_worsens"]


def test_contradiction_branches_llm_fallback() -> None:
    branches = build_contradiction_branches(True, [], ["叙事：AI 主线不破"])
    assert branches and branches[0]["topic"] == "叙事矛盾"


def test_contradiction_branches_empty_when_no_contradiction() -> None:
    assert build_contradiction_branches(False, [], []) == []


def test_verify_window_hit_and_miss() -> None:
    row = {
        "run_date": "2026-07-01",
        "code": "600519",
        "action": "buy",
        "verify_in_days": 14,
    }
    # 基价 100，验证期内最高 107 → hit
    hit = evaluate_verify_window(row, [100.0, 103.0, 107.0, 105.0], date(2026, 8, 1))
    assert hit["verdict"] == "hit"
    assert hit["max_up_pct"] >= 5.0
    # 基价 100，最低 90 → miss
    miss = evaluate_verify_window(row, [100.0, 95.0, 90.0, 92.0], date(2026, 8, 1))
    assert miss["verdict"] == "miss"


def test_verify_window_watch_avoidance() -> None:
    row = {
        "run_date": "2026-07-01",
        "code": "300750",
        "action": "watch",
        "verify_in_days": 14,
    }
    # watch 后大跌 → 规避成功
    avoided = evaluate_verify_window(row, [100.0, 92.0, 90.0], date(2026, 8, 1))
    assert avoided["verdict"] == "avoided"
    # watch 后横盘 → 规避未果
    flat = evaluate_verify_window(row, [100.0, 101.0, 99.0], date(2026, 8, 1))
    assert flat["verdict"] == "avoid_failed"


def test_verify_window_pending_before_due() -> None:
    row = {
        "run_date": "2026-08-05",
        "code": "600519",
        "action": "buy",
        "verify_in_days": 14,
    }
    out = evaluate_verify_window(row, [100.0, 101.0], date(2026, 8, 8))
    assert out["verdict"] == "pending"


def test_llm_stats_tracked() -> None:
    p = OpenAICompatProvider(
        name="t", api_key="k", base_url="https://x", model="m"
    )
    assert p.stats["calls"] == 0
    p.stats["calls"] += 1
    p.stats["finish_length"] += 1
    assert p.stats["finish_length"] == 1
