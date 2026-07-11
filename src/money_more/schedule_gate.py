"""周期运行门禁：按 interval_days 决定是否该跑完整流程。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path


LAST_RUN_FILE = "logs/last_full_run.txt"


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
) -> tuple[bool, str]:
    """返回 (是否应跑, 原因说明)。"""
    if force:
        return True, "force=true，跳过间隔门禁"
    today = today or date.today()
    interval = max(1, int(interval_days))
    last = read_last_run(project_root)
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
