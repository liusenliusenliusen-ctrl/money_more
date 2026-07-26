"""复盘规范化与 60 日语料。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from money_more.analysis.review_history import (
    build_action_lifecycles,
    load_historical_reports_corpus,
)
from money_more.analysis.review_normalize import (
    normalize_stock_review,
    outcome_from_status,
)
from money_more.report.writer import render_daily_report
from money_more.storage.db import Database


def test_normalize_blocks_pnl_as_failure() -> None:
    item = {
        "action": "hold",
        "return_pct": -12.5,
        "entry_price": 100,
        "current_price": 87.5,
        "invalidation_check": {"invalidated": False, "fired": []},
    }
    rv = normalize_stock_review(
        {
            "status": "wrong",
            "outcome": "wrong",
            "diagnosis": "跌了所以错",
            "process_quality": "unclear",
            "discipline": "n/a",
        },
        item=item,
    )
    assert rv["status"] == "tracking"
    assert rv["outcome"] == "tracking"
    assert "轨迹" in rv["diagnosis"] or "开放式" in rv["diagnosis"]


def test_normalize_invalidation_fired() -> None:
    item = {
        "action": "hold",
        "return_pct": -5.0,
        "invalidation_check": {"invalidated": True, "fired": ["跌破MA20"]},
    }
    rv = normalize_stock_review(
        {"status": "thesis_intact", "outcome": "correct", "diagnosis": "仍看好"},
        item=item,
    )
    assert rv["status"] == "invalidation_fired"
    assert outcome_from_status(rv["status"]) == "wrong"


def test_action_lifecycles_and_corpus(tmp_path: Path) -> None:
    dig = tmp_path / "digests"
    dig.mkdir()
    for i, (d, action) in enumerate(
        [("2026-06-01", "watch"), ("2026-06-15", "buy"), ("2026-07-01", "hold")]
    ):
        (dig / f"{d}.json").write_text(
            json.dumps(
                {
                    "market_phase": "range",
                    "recommendations": [
                        {"code": "601318", "action": action, "invalidation": "跌破MA20"}
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    corpus = load_historical_reports_corpus(
        tmp_path, as_of=date(2026, 7, 18), lookback_days=60, max_reports=40
    )
    assert corpus["digest_count"] == 3
    assert corpus["window"]["lookback_days"] == 60
    cycles = build_action_lifecycles(corpus["decision_digests"])
    assert len(cycles) == 1
    assert cycles[0]["code"] == "601318"
    assert cycles[0]["action_path"] == ["watch", "buy", "hold"]
    assert cycles[0]["changed"] is True


def test_review_upsert_tracking(tmp_path: Path) -> None:
    db = Database(tmp_path / "r.db")
    with db.session() as conn:
        conn.execute(
            "INSERT INTO daily_runs (id, run_date, status, started_at) VALUES (1, '2026-07-01', 'success', 't')"
        )
        conn.execute(
            """
            INSERT INTO recommendations
            (id, run_id, stock_code, action, confidence, position_pct, rationale, created_at)
            VALUES (10, 1, '601318', 'buy', 0.7, 10, 'x', 't')
            """
        )
    rid = db.save_review(
        run_id=1,
        recommendation_id=10,
        stock_code="601318",
        original_action="buy",
        outcome="tracking",
        return_pct=-3.0,
        diagnosis="跟踪中",
        lesson="",
    )
    rid2 = db.save_review(
        run_id=1,
        recommendation_id=10,
        stock_code="601318",
        original_action="buy",
        outcome="tracking",
        return_pct=-1.0,
        diagnosis="仍跟踪",
        lesson="保持纪律",
    )
    assert rid == rid2
    pending = db.get_recommendations_for_review(date(2026, 7, 18), 60)
    assert any(int(x["id"]) == 10 for x in pending)


def test_render_review_section_mentions_window() -> None:
    from money_more.report.writer import render_review_report

    md = render_review_report(
        {
            "run_date": "2026-07-18",
            "review_window": {
                "lookback_days": 60,
                "cutoff": "2026-05-19",
                "as_of": "2026-07-18",
            },
            "review_window_note": "材料完整",
            "dimension_reviews": [
                {
                    "dimension": "market",
                    "subject": "震荡",
                    "outcome": "partial",
                    "diagnosis": "基本延续",
                    "process_quality": "process_ok",
                }
            ],
            "reviews": [
                {
                    "stock_code": "601318",
                    "status": "tracking",
                    "outcome": "tracking",
                    "return_pct": -2.0,
                    "diagnosis": "thesis 仍在",
                    "process_quality": "process_ok",
                    "discipline": "discipline_ok",
                }
            ],
            "recommendations": [],
            "market": {},
            "sectors": [],
            "stocks": [],
        }
    )
    assert "取材窗口" in md
    assert "浮盈亏只作轨迹" in md
    assert "个股动作复盘" in md
    assert "status=`tracking`" in md
