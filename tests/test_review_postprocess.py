"""复盘后处理：已吸收、经验归并、无信号观察、窗口说明。"""

from money_more.analysis.review_postprocess import (
    fold_lessons,
    load_digest_verify_signals,
    mark_absorbed_failures,
    resolve_verify_signals,
    sanitize_window_note,
    watches_without_checkable_signals,
)
from money_more.analysis.sector_map import sector_labels_in_text
from money_more.analysis.wave2_enrich import build_dimension_diff_table


def test_absorbed_failures_leave_fresh_mistakes() -> None:
    rows = [
        {
            "what_failed": [
                "8/5 批次 buy 后改 watch",
                "医药优先级来回跳",
            ]
        }
    ]
    mark_absorbed_failures(rows)
    assert rows[0]["what_failed"] == ["医药优先级来回跳"]
    assert "8/5" in rows[0]["absorbed"][0]


def test_fold_near_duplicate_lessons() -> None:
    dims = [{"lesson": "微观结构禁新开仓优先于宏观阶段判断"}]
    stocks = [
        {"lesson": "微观结构禁新开仓优先于宏观阶段判断。"},
        {"lesson": "资源股要跟踪铜价"},
    ]
    kept = fold_lessons(dims, stocks)
    assert len(kept) == 2
    assert stocks[0]["lesson_folded"] is True
    assert stocks[1]["lesson_folded"] is False


def test_watch_without_any_signal_is_pending() -> None:
    reviews = [{"recommendation_id": 1, "status": "tracking", "outcome": "tracking"}]
    pending = [{"id": 1, "action": "watch", "verify_signals": []}]
    watches_without_checkable_signals(reviews, pending)
    assert reviews[0]["status"] == "pending"
    assert reviews[0]["material_gap"] == "未记录验证信号"


def test_narrative_signal_is_not_stamped_missing() -> None:
    reviews = [{"recommendation_id": 1, "status": "tracking", "outcome": "tracking"}]
    pending = [{"id": 1, "action": "watch", "verify_signals": ["中秋动销超预期"]}]
    watches_without_checkable_signals(reviews, pending)
    assert reviews[0]["status"] == "tracking"
    assert "material_gap" not in reviews[0]


def test_old_recommendation_signals_come_from_digest(tmp_path) -> None:
    day = tmp_path / "2026-10-30.json"
    day.write_text(
        '{"run_date":"2026-10-30","recommendations":[{"code":"600519","verify_signals":["飞天批价企稳"]}]}',
        encoding="utf-8",
    )
    index = load_digest_verify_signals(tmp_path)
    assert resolve_verify_signals(None, "2026-10-30", "600519", index) == ["飞天批价企稳"]
    assert resolve_verify_signals(["库内信号"], "2026-10-30", "600519", index) == ["库内信号"]


def test_window_note_conflict_replaced_by_anchor() -> None:
    anchor = "本轮视图（必须引用）：阶段=range，美债10Y=5.11%"
    assert sanitize_window_note("9-25当日无新增分析，以9-22为对照", anchor) == anchor
    assert sanitize_window_note("材料较完整", anchor) == "材料较完整"


def test_sector_labels_from_sentence() -> None:
    labels = sector_labels_in_text("新能源早期 high，电池优先级消失，医药未预埋")
    assert "新能源" in labels and "医药" in labels


def test_sector_priority_exit_is_not_unknown() -> None:
    table = build_dimension_diff_table(
        [
            {
                "date": "2026-07-01",
                "market": {"phase": "range", "style": "value", "risk_level": "high"},
                "sectors": [
                    {"sector": "电池", "priority": "high"},
                    {"sector": "银行", "priority": "medium"},
                ],
            }
        ],
        {
            "market": {"phase": "range", "style": "value", "risk_level": "high"},
            "sectors": [
                {"sector": "银行", "priority": "medium"},
                {"sector": "医药", "priority": "low"},
            ],
        },
    )
    by = {r.get("sector"): r for r in table if r.get("dimension") == "sector"}
    assert by["电池"]["verdict"] == "exited"
    assert by["电池"]["now"] == "退出跟踪"
    assert by["医药"]["verdict"] == "new"
    assert by["银行"]["verdict"] == "stable"
