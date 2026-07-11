"""自优化与人工/Cursor CLI 改动的冲突防护。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


# 人工正在用 Cursor CLI / IDE 改代码时，创建此空文件即可暂停周期自优化
PAUSE_LOCK_REL = "logs/OPTIMIZE_PAUSE"


@dataclass
class WorkspaceSnapshot:
    pause_lock: bool
    dirty_paths: list[str]
    recent_commits: list[str]

    @property
    def is_dirty(self) -> bool:
        return bool(self.dirty_paths)


def pause_lock_path(project_root: Path) -> Path:
    return project_root / PAUSE_LOCK_REL


def _git(cwd: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def list_dirty_paths(project_root: Path) -> list[str]:
    """未提交改动的路径（含 untracked），忽略 logs/reports 噪声。"""
    out = _git(project_root, "status", "--porcelain", "-u")
    if not out:
        return []
    ignore_prefixes = ("logs/", "reports/", ".env")
    paths: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        # porcelain: XY PATH 或 XY ORIG -> PATH
        rest = line[3:].strip()
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        path = rest.strip().strip('"')
        if not path:
            continue
        if any(path == p or path.startswith(p) for p in ignore_prefixes):
            continue
        paths.append(path)
    return paths


def recent_commit_subjects(project_root: Path, n: int = 5) -> list[str]:
    out = _git(project_root, "log", f"-{n}", "--pretty=format:%h %s")
    if not out:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def snapshot_workspace(project_root: Path) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        pause_lock=pause_lock_path(project_root).exists(),
        dirty_paths=list_dirty_paths(project_root),
        recent_commits=recent_commit_subjects(project_root),
    )


def should_skip_optimize(
    project_root: Path,
    *,
    skip_if_dirty: bool = True,
    respect_human_lock: bool = True,
) -> tuple[bool, str]:
    snap = snapshot_workspace(project_root)
    if respect_human_lock and snap.pause_lock:
        return (
            True,
            f"检测到 {PAUSE_LOCK_REL}：人工/Cursor CLI 改动中，跳过自优化。"
            "完成后删除该文件即可恢复。",
        )
    if skip_if_dirty and snap.is_dirty:
        preview = ", ".join(snap.dirty_paths[:8])
        more = f" 等{len(snap.dirty_paths)}个文件" if len(snap.dirty_paths) > 8 else ""
        return (
            True,
            f"工作区有未提交改动（可能含人工/CLI 编辑），跳过自优化以免冲突: {preview}{more}。"
            "提交或 stash 后重试；紧急可设 optimize.skip_if_dirty=false。",
        )
    return False, "工作区可安全自优化"


def format_collab_context(snap: WorkspaceSnapshot) -> str:
    lines = [
        "## 与人工协作（重要）",
        "- 维护者也会用 **Cursor CLI / IDE** 手工改代码；自优化不得覆盖、回滚、重写其未完成工作。",
        "- **禁止** `git reset --hard`、`git checkout --`、`git clean -fd`、强制覆盖未读过的本地改动。",
        "- **禁止** git commit / push；不要改 `.env` 密钥。",
        "- 改前先读 `logs/optimization_progress.txt`；只 **追加** 本轮记录，不要删掉人工笔记。",
        "- 若某文件近期刚被人工改过且你不确定意图，跳过该文件，改别的高 ROI 点。",
        f"- 暂停锁文件: `{PAUSE_LOCK_REL}`（存在则本应已跳过；若仍运行请立刻停止改代码）。",
    ]
    if snap.dirty_paths:
        lines.append("- 当前未提交路径（尽量避开）:")
        for p in snap.dirty_paths[:20]:
            lines.append(f"  - `{p}`")
    else:
        lines.append("- 当前工作区相对干净（已忽略 logs/reports）。")
    if snap.recent_commits:
        lines.append("- 最近提交（避免重复/打架）:")
        for c in snap.recent_commits:
            lines.append(f"  - {c}")
    return "\n".join(lines)
