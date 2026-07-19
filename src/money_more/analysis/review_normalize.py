"""复盘结果规范化：开放式预测下，禁止用浮盈亏单独判失败。"""

from __future__ import annotations

from typing import Any

# 终局状态：写入后不再反复复盘同一 recommendation
TERMINAL_STATUSES = frozenset(
    {
        "closed",
        "invalidation_fired",
        "discipline_fail",
        "process_error",
        "correct",  # 兼容旧 outcome
        "wrong",
        "partial",
    }
)

# 可再跟踪
TRACKING_STATUSES = frozenset(
    {
        "tracking",
        "pending",
        "thesis_intact",
        "thesis_intact_tracking",
    }
)

_STATUS_ALIASES = {
    "ok": "thesis_intact",
    "intact": "thesis_intact",
    "alive": "thesis_intact",
    "fired": "invalidation_fired",
    "invalidated": "invalidation_fired",
    "fail_discipline": "discipline_fail",
    "discipline_failed": "discipline_fail",
    "process_fail": "process_error",
    "logic_error": "process_error",
    "in_progress": "tracking",
    "open": "tracking",
}


def normalize_status(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if not s:
        return "pending"
    s = _STATUS_ALIASES.get(s, s)
    allowed = TERMINAL_STATUSES | TRACKING_STATUSES | {"discipline_ok", "process_ok", "linkage_ok", "linkage_error"}
    # discipline_ok / process_ok 不是个股结案状态
    if s in ("discipline_ok", "process_ok", "linkage_ok"):
        return "thesis_intact"
    if s == "linkage_error":
        return "process_error"
    if s in TERMINAL_STATUSES or s in TRACKING_STATUSES:
        return s
    # 旧 outcome 映射
    if s in ("correct", "wrong", "partial", "pending"):
        return s
    return "tracking"


def outcome_from_status(status: str) -> str:
    """写入 reviews.outcome 的兼容字段。"""
    status = normalize_status(status)
    if status in ("correct", "wrong", "partial", "pending"):
        return status
    if status in TRACKING_STATUSES:
        return "tracking" if status != "pending" else "pending"
    if status == "invalidation_fired":
        return "wrong"  # 条件证伪 → 旧口径 wrong
    if status == "discipline_fail":
        return "wrong"
    if status == "process_error":
        return "wrong"
    if status == "closed":
        return "partial"
    return "tracking"


def normalize_stock_review(
    rv: dict[str, Any],
    *,
    item: dict[str, Any],
) -> dict[str, Any]:
    """纠正模型用涨跌代替判断的情况。"""
    out = dict(rv)
    status = normalize_status(out.get("status") or out.get("outcome"))
    inv = item.get("invalidation_check") or {}
    inv_fired = bool(inv.get("invalidated") or inv.get("fired"))
    return_pct = item.get("return_pct")
    action = str(item.get("action") or "").lower()

    # 失效已触发 → 不得标成 thesis_intact / correct
    if inv_fired and status in ("thesis_intact", "tracking", "correct", "partial", "pending"):
        status = "invalidation_fired"
        out.setdefault(
            "diagnosis",
            "失效条件已触发，原 thesis 应终止或改口。",
        )

    # 未触发失效且未给 process_error：禁止仅因浮亏打 wrong/correct
    if not inv_fired and status in ("wrong", "correct", "partial"):
        pq = str(out.get("process_quality") or "").lower()
        dq = str(out.get("discipline") or "").lower()
        if pq in ("process_error",) or dq in ("discipline_fail",):
            status = "process_error" if "process" in pq else "discipline_fail"
        else:
            # 有浮盈亏只作轨迹
            status = "tracking"
            note = (
                f"开放式预测：收益 {return_pct}% 仅作轨迹跟踪，不据此判定预测成败。"
                if return_pct is not None
                else "开放式预测：未触发失效条件，维持 tracking。"
            )
            prev = str(out.get("diagnosis") or "").strip()
            out["diagnosis"] = f"{prev} {note}".strip() if prev else note

    # watch 且无仓：更偏过程/链路
    if action == "watch" and status in ("wrong", "correct"):
        status = "tracking"

    out["status"] = status
    out["outcome"] = outcome_from_status(status)
    out.setdefault("process_quality", out.get("process_quality") or "unclear")
    out.setdefault("linkage_quality", out.get("linkage_quality") or "unclear")
    out.setdefault("discipline", out.get("discipline") or "n/a")
    out["tracking_metrics"] = {
        "return_pct": return_pct,
        "entry_price": item.get("entry_price"),
        "current_price": item.get("current_price"),
        "note": "轨迹指标，不等于预测成败",
    }
    return out


def normalize_dimension_review(dr: dict[str, Any]) -> dict[str, Any]:
    out = dict(dr)
    # 维度仍可用 correct/partial/wrong/pending；补充 process_quality
    oc = str(out.get("outcome") or "pending").lower()
    if oc not in ("correct", "partial", "wrong", "pending"):
        out["outcome"] = "pending"
    out.setdefault("process_quality", "unclear")
    return out
