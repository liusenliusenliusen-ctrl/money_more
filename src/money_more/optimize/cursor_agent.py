"""调用 Cursor Agent SDK，对 money_more 做一轮代码级优化。"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from money_more.config import AppConfig
from money_more.optimize.workspace_guard import (
    format_collab_context,
    should_skip_optimize,
    snapshot_workspace,
)
from money_more.utils.logging_util import setup_logging

log = setup_logging()


OPTIMIZE_PROMPT_TEMPLATE = """你是 money_more（A股中长线 AI 研究助手）的代码优化工程师。

## 项目目标
- 投资取向：**中长线**（数周到数季），不是短线/日内
- 运行频率：**每 5 天一次**（节省 token）
- 流程：情报 → 市场/板块/个股分析 → 建议 → 复盘 → 趋势 →（本步）代码自优化

## 本期已产出
- 最新报告目录: reports/
- 最新日期报告: reports/{run_date}.md （若存在）
- 趋势报告: reports/trend.md
- 决策摘要: reports/digests/{run_date}.json （若存在）
- 进度笔记: logs/optimization_progress.txt （若存在）

{collab_block}

## 你的任务（必须改代码，不要只写建议）
1. 阅读最新报告与 `src/money_more/` 关键模块，找出 **可落地** 的优化点，优先：
   - 增加/加固中长线相关信息源（基本面、估值分位、产业数据）
   - 增加分析维度（但避免短线噪声维度）
   - 改进数据处理、缓存、质量门禁、复盘严谨性
   - 修复明显 bug / 降低 LLM token 浪费
2. 本轮只做 **1–3 个高 ROI 改动**，保持小而完整，附带或更新单元测试
3. **追加** 更新 `logs/optimization_progress.txt`（保留既有人工/历史记录）
4. 在回复末尾用 Markdown 写一份「本轮优化报告」摘要（改了什么、为什么、如何验证）
5. 不要提交 git、不要改 .env 里的密钥、不要扩大成短线交易系统

## 约束
- 保持中长线 + 每5天周期设定（config 中 investment_horizon / schedule.interval_days）
- 改完后确保 `python -m pytest tests/ -q` 能过（若环境有 pytest）
"""


def build_optimize_prompt(run_date: str | None = None, collab_block: str = "") -> str:
    d = run_date or date.today().isoformat()
    return OPTIMIZE_PROMPT_TEMPLATE.format(
        run_date=d,
        collab_block=collab_block or "## 与人工协作\n- 避免覆盖人工未提交改动。",
    )


def _write_optimize_report(
    config: AppConfig,
    run_date: str,
    out: dict[str, Any],
) -> Path:
    """把 Cursor 优化结果写成 reports/optimize-YYYY-MM-DD.md。"""
    reports = config.resolve(config.paths.reports)
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"optimize-{run_date}.md"
    status = out.get("status") or ("skipped" if out.get("skipped") else "unknown")
    body = out.get("result_text") or out.get("error") or out.get("reason") or "(无正文)"
    md = (
        f"# money_more 自优化报告\n\n"
        f"- 日期: {run_date}\n"
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}\n"
        f"- 状态: `{status}`\n"
        f"- Cursor run_id: `{out.get('run_id') or '-'}`\n"
        f"- 模型: `{config.optimize.model}`\n\n"
        f"---\n\n"
        f"{body}\n"
    )
    path.write_text(md, encoding="utf-8")
    return path


def run_cursor_optimize(config: AppConfig, run_date: str | None = None) -> dict[str, Any]:
    """同步调用 Cursor SDK 本地 Agent 做一轮优化。"""
    d = run_date or date.today().isoformat()

    if not config.optimize.enabled:
        out = {"skipped": True, "reason": "optimize.enabled=false"}
        out["report_path"] = str(_write_optimize_report(config, d, out))
        return out

    skip, reason = should_skip_optimize(
        config.project_root,
        skip_if_dirty=config.optimize.skip_if_dirty,
        respect_human_lock=config.optimize.respect_human_lock,
    )
    if skip:
        log.warning("optimize skipped: %s", reason)
        out = {"skipped": True, "reason": reason}
        out["report_path"] = str(_write_optimize_report(config, d, out))
        return out

    api_key = (config.cursor_api_key or os.getenv("CURSOR_API_KEY") or "").strip()
    if not api_key or api_key.startswith("your_"):
        out = {
            "skipped": True,
            "reason": "未配置 CURSOR_API_KEY，请写入 .env 后重试 money-more optimize",
        }
        out["report_path"] = str(_write_optimize_report(config, d, out))
        return out

    try:
        from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions
    except ImportError:
        out = {
            "skipped": True,
            "reason": "未安装 cursor-sdk，请执行: pip install cursor-sdk",
        }
        out["report_path"] = str(_write_optimize_report(config, d, out))
        return out

    snap = snapshot_workspace(config.project_root)
    prompt = build_optimize_prompt(d, collab_block=format_collab_context(snap))
    cwd = str(config.project_root.resolve())
    model = config.optimize.model
    log.info("Starting Cursor optimize model=%s cwd=%s", model, cwd)

    try:
        with Agent.create(
            model=model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=cwd),
        ) as agent:
            run = agent.send(prompt)
            # 可选流式日志
            try:
                for message in run.messages():
                    if getattr(message, "type", None) == "assistant":
                        content = getattr(message, "message", None)
                        if content is None:
                            continue
                        blocks = getattr(content, "content", None) or []
                        for block in blocks:
                            if getattr(block, "type", None) == "text":
                                text = getattr(block, "text", "") or ""
                                if text:
                                    log.info("optimize: %s", text[:200])
            except Exception as stream_exc:
                log.warning("stream read partial: %s", stream_exc)

            result = run.wait()
            status = getattr(result, "status", None) or str(result)
            out: dict[str, Any] = {
                "skipped": False,
                "status": status,
                "run_id": getattr(result, "id", None),
                "result_text": getattr(result, "result", None) or getattr(result, "text", None),
            }
            # 落盘
            logs = config.project_root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "last_optimize.txt").write_text(
                f"status={status}\nrun_id={out.get('run_id')}\n\n{out.get('result_text') or ''}",
                encoding="utf-8",
            )
            out["report_path"] = str(_write_optimize_report(config, d, out))
            return out
    except CursorAgentError as err:
        out = {
            "skipped": False,
            "status": "startup_error",
            "error": getattr(err, "message", str(err)),
            "retryable": getattr(err, "is_retryable", None),
        }
        out["report_path"] = str(_write_optimize_report(config, d, out))
        return out
    except Exception as exc:
        out = {"skipped": False, "status": "error", "error": str(exc)}
        out["report_path"] = str(_write_optimize_report(config, d, out))
        return out
