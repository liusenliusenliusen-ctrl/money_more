"""周期运行门禁：按 cadence / interval_days 决定是否该跑完整流程。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path


LAST_RUN_FILE = "logs/last_full_run.txt"

# Python date.weekday(): Mon=0 … Sun=6
_TUE_FRI = frozenset({1, 4})  # Tuesday, Friday


def last_run_path(project_root: Path) -> Path:
    return project_root / LAST_RUN_FILE


def read_last_run(project_root: Path) -> date | None:
    path = last_run_path(project_root)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip().splitlines()
    if not text:
        return None
    try:
        return date.fromisoformat(text[0].strip()[:10])
    except ValueError:
        return None


def write_last_run(project_root: Path, run_date: date | None = None) -> Path:
    path = last_run_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = run_date or date.today()
    path.write_text(
        f"{d.isoformat()}\nupdated_at={datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8",
    )
    return path


def should_run(
    project_root: Path,
    interval_days: int = 5,
    today: date | None = None,
    force: bool = False,
    cadence: str = "every_5_days",
) -> tuple[bool, str]:
    """返回 (是否应跑, 原因说明)。

    cadence:
      - tue_fri / tuesday_friday: 仅周二、周五（由外部定时在 01:00 触发）
      - every_5_days / weekly / daily / 其它: 按 interval_days 间隔门禁
    """
    if force:
        return True, "force=true，跳过间隔门禁"
    today = today or date.today()
    cadence_n = str(cadence or "every_5_days").strip().lower()
    last = read_last_run(project_root)

    if cadence_n in ("tue_fri", "tuesday_friday"):
        if today.weekday() not in _TUE_FRI:
            names = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
            return (
                False,
                f"今日 {today.isoformat()}（{names.get(today.weekday(), '?')}）非周二/周五排期，跳过",
            )
        if last == today:
            return False, f"今日 {today.isoformat()} 已成功跑过，跳过重复触发"
        return True, f"周二/周五排期（{today.isoformat()}），允许运行"

    interval = max(1, int(interval_days))
    if last is None:
        return True, "尚无成功记录，允许首次运行"
    elapsed = (today - last).days
    if elapsed >= interval:
        return True, f"距上次 {last.isoformat()} 已过 {elapsed} 天（间隔 {interval} 天）"
    next_due = last + timedelta(days=interval)
    return (
        False,
        f"距上次 {last.isoformat()} 仅 {elapsed} 天，下次应跑 {next_due.isoformat()}（间隔 {interval} 天）",
    )
