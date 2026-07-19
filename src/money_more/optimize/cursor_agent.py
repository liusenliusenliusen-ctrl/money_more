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


DATA_SOURCE_CHECKLIST = """
## 数据源自检清单（每轮优化必读）

应用效果 = **数据源广度/质量** × **分析能力**。本轮若报告有 data_quality 缺失项，优先补数据；否则按清单找最弱一环。

| 维度 | 已有能力（勿重复造轮子） | 可优化方向 |
|------|--------------------------|------------|
| 宏观 | 新闻联播、东财/新浪全球、PMI/CPI/M2、经济日历(含合成)、两融 | 新宏观 API、日历备源、数据新鲜度 |
| 基本面 | Tushare 财报/公告/forecast、daily_basic 估值分位、AkShare 财务摘要 | 免费替代源、分位窗口、缺失回填 |
| 交易/资金 | 行情/K线、北向、板块资金流、RS vs 沪深300、ATR 仓位 | 北向新鲜度、资金流多源交叉 |
| 舆情/情绪 | 财联社/同花顺/富途快讯、RSS、词典+规则舆情打分(0-100)、东财人气/千股千评 | **重点可加强**：社交舆情、行业情绪指数、事件情感、拥挤度 |
| 产业/主题 | 板块排名、概念板、RSS 关键词匹配 | 产业政策库、景气度指标 |
| 质量门禁 | as_of、双源校验、data_quality 评分、Tushare 不可用时的 CLS/RSS/东财补位 | 减少 false missing、备源降级策略 |
"""


OPTIMIZE_PROMPT_TEMPLATE = """你是 money_more（A股中长线 AI 研究助手）的代码优化工程师。

## 项目目标
- 投资取向：**中长线**（数周到数季），不是短线/日内
- 运行频率：**每 5 天一次**（节省 token）
- 流程：情报 → 市场/板块/个股分析 → 建议 → 复盘 → 趋势 →（本步）代码自优化
- **核心认知**：应用效果取决于两方面——**(1) 数据源越丰富越好**（宏观/基本面/交易/舆情等）；(2) 分析框架与 LLM 用法。自优化时 **优先补数据短板**，再改分析逻辑。

## 本期已产出
- 最新报告目录: reports/
- 最新日期报告: reports/{run_date}.md （若存在）
- 趋势报告: reports/trend.md
- 决策摘要: reports/digests/{run_date}.json （若存在）
- 进度笔记: logs/optimization_progress.txt （若存在）

{run_context}

{data_source_checklist}

{collab_block}

## 你的任务（必须改代码，不要只写建议）
1. 阅读最新报告、`data_quality` 缺失项与 `src/money_more/data/`、`analysis/` 模块
2. 找出 **可落地** 的高 ROI 优化，**优先级**：
   - **P0 数据源**：增加/加固/备源/回填（宏观、基本面、交易数据、**舆情情绪**）；Tushare 不可用时的免费替代
   - **P1 分析能力**：因子、交叉验证、复盘、prompt 维度（避免短线噪声）
   - **P2 工程**：缓存、质量门禁、token 压缩、bug 修复
3. 舆情方向特别关注：快讯覆盖、情感打分准确性、人气/拥挤度、事件驱动标签、与宏观/个股链路的打通
4. 本轮只做 **1–3 个** 改动，小而完整，附带或更新单元测试
5. **追加** 更新 `logs/optimization_progress.txt`（保留人工/历史记录；可记「数据源/分析」分类）
6. 回复末尾写「本轮优化报告」：改了什么、补了哪类数据、如何验证
7. 不要提交 git、不要改 .env 密钥、不要扩大成短线交易系统

## 约束
- 保持中长线 + 每5天周期（config: investment_horizon / schedule.interval_days）
- 新增数据源优先 AkShare/公开 RSS/免费 API；付费源（Tushare 权限）仅作可选增强
- 改完后确保 `python -m pytest tests/ -q` 能过
"""


def _extract_report_context(project_root: Path, run_date: str) -> str:
    """从最新报告提取 data_quality 等上下文，供自优化 prompt 使用。"""
    reports = project_root / "reports"
    md_path = reports / f"{run_date}.md"
    if not md_path.exists():
        return ""
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines: list[str] = ["## 本期报告摘要（供优化参考）"]
    for line in text.splitlines()[:80]:
        stripped = line.strip()
        if stripped.startswith("**数据质量**") or stripped.startswith("- 缺失项:"):
            lines.append(stripped)
        if stripped.startswith("**量化舆情分**"):
            lines.append(stripped)
    digest = reports / "digests" / f"{run_date}.json"
    if digest.exists():
        lines.append(f"- digest: reports/digests/{run_date}.json")
    if len(lines) <= 1:
        return ""
    return "\n".join(lines) + "\n"


def build_optimize_prompt(
    run_date: str | None = None,
    collab_block: str = "",
    project_root: Path | None = None,
) -> str:
    d = run_date or date.today().isoformat()
    root = project_root or Path.cwd()
    run_context = _extract_report_context(root, d)
    return OPTIMIZE_PROMPT_TEMPLATE.format(
        run_date=d,
        run_context=run_context or "（暂无本期报告摘要）",
        data_source_checklist=DATA_SOURCE_CHECKLIST.strip(),
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
    prompt = build_optimize_prompt(
        d,
        collab_block=format_collab_context(snap),
        project_root=config.project_root,
    )
    cwd = str(config.project_root.resolve())
    model = config.optimize.model
    log.info("Starting Cursor optimize model=%s cwd=%s", model, cwd)

    try:
        from money_more.llm.timeout_util import LLMTimeoutError, run_with_timeout

        wait_s = max(60.0, float(config.optimize.max_minutes) * 60.0)
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

            result = run_with_timeout(run.wait, wait_s)
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
    except LLMTimeoutError as err:
        out = {
            "skipped": False,
            "status": "timeout",
            "error": str(err),
            "retryable": True,
        }
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
