from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from money_more.analysis.pipeline import DecisionPipeline
from money_more.config import load_config
from money_more.data.fetcher import MarketDataFetcher
from money_more.llm.client import LLMClient
from money_more.report.writer import render_daily_report, render_trend_report, save_report
from money_more.storage.db import Database


console = Console()


def _ensure_src_on_path() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.resolve(config.paths.db))
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    fetcher = MarketDataFetcher(as_of=run_date)
    llm = LLMClient(config)
    pipeline = DecisionPipeline(config, db, fetcher, llm)

    cadence = config.schedule.cadence
    label = {
        "every_5_days": f"每{config.schedule.interval_days}天",
        "weekly": "周度",
        "daily": "每日",
    }.get(cadence, cadence)
    console.print(
        Panel(
            f"开始{label}分析（{config.analysis.investment_horizon}）: {run_date.isoformat()}",
            title="money_more",
        )
    )

    try:
        # 可选跳过辩论
        if getattr(args, "skip_debate", False):
            pipeline.config.analysis.debate_top_k = 0
        result = pipeline.run_daily(run_date)
        report_path = save_report(result, config.resolve(config.paths.reports))
        db.finish_run(result["run_id"], "success", str(report_path))
        console.print("\n" + render_daily_report(result))
        console.print(Panel(f"分析报告已保存: {report_path}", style="green"))
        if result.get("trend"):
            console.print(Panel("趋势报告已更新: reports/trend.md", style="cyan"))
        dq = result.get("data_quality") or {}
        if dq.get("degraded"):
            console.print(Panel(dq.get("note", "数据降级"), title="数据质量", style="yellow"))

        if config.email.enabled and config.email.send_analysis:
            from money_more.notify import notify_analysis_report

            mail = notify_analysis_report(config, report_path, run_date.isoformat())
            if mail.get("skipped"):
                console.print(Panel(f"邮件跳过: {mail.get('reason')}", style="yellow"))
            elif mail.get("ok"):
                msg = f"分析报告已发邮件 → {mail.get('to')}"
                if mail.get("guide_sent_to"):
                    msg += f"\n首次附带解读文档 → {mail.get('guide_sent_to')}"
                console.print(Panel(msg, style="green"))
            else:
                console.print(Panel(f"邮件发送失败: {mail.get('error')}", style="red"))

        # 周期流程可选：跑完后调用 Cursor 自优化并写优化报告
        if getattr(args, "optimize", False) or (
            config.schedule.optimize_after_run and getattr(args, "with_optimize", False)
        ):
            from money_more.optimize import run_cursor_optimize

            console.print(Panel("开始 Cursor Agent 代码优化…", style="cyan"))
            opt = run_cursor_optimize(config, run_date.isoformat())
            console.print(Panel(str(opt), title="optimize", style="green" if not opt.get("error") else "red"))
            if opt.get("report_path"):
                console.print(Panel(f"优化报告已保存: {opt['report_path']}", style="cyan"))
                if config.email.enabled and config.email.send_optimize:
                    from money_more.notify import notify_optimize_report

                    mail = notify_optimize_report(config, opt["report_path"], run_date.isoformat())
                    if mail.get("skipped"):
                        console.print(Panel(f"邮件跳过: {mail.get('reason')}", style="yellow"))
                    elif mail.get("ok"):
                        console.print(Panel(f"优化报告已发邮件 → {mail.get('to')}", style="green"))
                    else:
                        console.print(Panel(f"邮件发送失败: {mail.get('error')}", style="red"))
        return 0
    except Exception as exc:
        console.print(Panel(str(exc), title="运行失败", style="red"))
        return 1


def cmd_optimize(args: argparse.Namespace) -> int:
    from money_more.optimize import run_cursor_optimize

    config = load_config(args.config)
    run_date = args.date or date.today().isoformat()
    console.print(Panel(f"Cursor 自优化 as_of={run_date}", title="money_more optimize"))
    opt = run_cursor_optimize(config, run_date)
    console.print(Panel(str(opt), title="result"))
    if opt.get("report_path"):
        console.print(Panel(f"优化报告: {opt['report_path']}", style="cyan"))
        if config.email.enabled and config.email.send_optimize and not opt.get("skipped"):
            from money_more.notify import notify_optimize_report

            mail = notify_optimize_report(config, opt["report_path"], run_date)
            if mail.get("ok"):
                console.print(Panel(f"优化报告已发邮件 → {mail.get('to')}", style="green"))
            elif not mail.get("skipped"):
                console.print(Panel(f"邮件发送失败: {mail.get('error')}", style="red"))
    if opt.get("skipped"):
        console.print(f"[yellow]{opt.get('reason')}[/yellow]")
        return 1
    if opt.get("status") in ("error", "startup_error") or opt.get("error"):
        return 2
    return 0


def cmd_email_test(args: argparse.Namespace) -> int:
    from money_more.notify import email_ready
    from money_more.notify.emailer import _send_with_optional_guide

    config = load_config(args.config)
    ok, reason = email_ready(config.email)
    if not ok:
        console.print(Panel(reason, title="邮件未就绪", style="red"))
        return 1
    mail = _send_with_optional_guide(
        config,
        subject="[money_more] 邮件测试",
        body=(
            "这是一封 money_more 测试邮件。若收到说明 SMTP 配置正确。\n"
            "若这是该收件人首次收到本系统邮件，将附带《如何解读报告》。"
        ),
        attachments=[],
        kind="test",
    )
    if mail.get("ok"):
        guide_to = mail.get("guide_sent_to") or []
        extra = f"\n首次附带解读文档 → {guide_to}" if guide_to else "\n（收件人均已收过解读文档，未再附送）"
        console.print(Panel(f"已发送 → {mail.get('to')}{extra}", style="green"))
        return 0
    console.print(Panel(str(mail.get("error") or mail), title="发送失败", style="red"))
    return 2


def cmd_scheduled(args: argparse.Namespace) -> int:
    """周期流程：按 interval_days 门禁 → 分析报告 + Cursor 优化报告。"""
    from money_more.schedule_gate import should_run, write_last_run

    config = load_config(args.config)
    force = bool(getattr(args, "force", False))
    ok, reason = should_run(
        config.project_root,
        interval_days=config.schedule.interval_days,
        force=force,
    )
    if not ok:
        console.print(Panel(reason, title="跳过本次（未到间隔）", style="yellow"))
        return 0

    console.print(Panel(reason, title="周期门禁", style="cyan"))
    args.optimize = False
    args.with_optimize = not getattr(args, "skip_optimize", False)
    args.skip_debate = getattr(args, "skip_debate", False)
    code = cmd_run(args)
    if code == 0:
        run_date = date.fromisoformat(args.date) if args.date else date.today()
        write_last_run(config.project_root, run_date)
        console.print(Panel(f"已记录成功运行日: {run_date.isoformat()}", style="green"))
    return code


def cmd_weekly(args: argparse.Namespace) -> int:
    """兼容旧命令名 → scheduled。"""
    return cmd_scheduled(args)


def cmd_review(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.resolve(config.paths.db))
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    fetcher = MarketDataFetcher(as_of=run_date)
    llm = LLMClient(config)
    pipeline = DecisionPipeline(config, db, fetcher, llm)

    # 复盘不清理当日分析快照
    run_id = db.ensure_run(run_date, mode="review")
    fetcher.set_as_of(run_date)
    result = pipeline.run_review(run_id, run_date)
    db.finish_run(run_id, "review_only")

    dims = result.get("dimension_reviews") or []
    if dims:
        dtable = Table(title="维度复盘")
        dtable.add_column("维度")
        dtable.add_column("对象")
        dtable.add_column("结果")
        dtable.add_column("诊断")
        for dr in dims:
            dtable.add_row(
                str(dr.get("dimension")),
                str(dr.get("subject") or "")[:24],
                str(dr.get("outcome")),
                str(dr.get("diagnosis") or "")[:40],
            )
        console.print(dtable)

    table = Table(title="个股复盘")
    table.add_column("代码")
    table.add_column("结果")
    table.add_column("收益%")
    table.add_column("诊断")
    for rv in result.get("reviews") or []:
        table.add_row(
            str(rv.get("stock_code")),
            str(rv.get("outcome")),
            str(rv.get("return_pct")),
            str(rv.get("diagnosis", ""))[:40],
        )
    console.print(table)
    return 0


def cmd_lessons(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.resolve(config.paths.db))
    lessons = db.get_active_lessons(limit=args.limit)

    table = Table(title="经验库")
    table.add_column("类别")
    table.add_column("内容")
    table.add_column("时间")
    for item in lessons:
        table.add_row(item.get("category", ""), item.get("content", ""), item.get("created_at", ""))
    console.print(table)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.resolve(config.paths.db))
    recs = db.get_recent_recommendations(limit=args.limit)

    table = Table(title="近期建议")
    table.add_column("日期")
    table.add_column("代码")
    table.add_column("动作")
    table.add_column("置信度")
    table.add_column("理由")
    for r in recs:
        table.add_row(
            r.get("run_date", ""),
            r.get("stock_code", ""),
            r.get("action", ""),
            str(r.get("confidence", "")),
            (r.get("rationale") or "")[:50],
        )
    console.print(table)
    return 0


def cmd_trend(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.resolve(config.paths.db))
    trend = db.get_trend_report()
    if not trend:
        console.print(Panel("尚无趋势报告。请先运行 money-more run。", style="yellow"))
        return 1
    console.print(render_trend_report(trend))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.resolve(config.paths.db))
    stats = db.get_paper_trade_stats()
    table = Table(title="纸面交易统计（含粗略交易成本）")
    table.add_column("指标")
    table.add_column("值")
    table.add_row("总笔数", str(stats.get("total")))
    table.add_row("持仓中", str(stats.get("open")))
    table.add_row("已平仓", str(stats.get("closed")))
    table.add_row("胜率", str(stats.get("hit_rate")))
    table.add_row("平均收益%", str(stats.get("avg_return_pct")))
    console.print(table)
    by_action = stats.get("avg_by_action") or {}
    if by_action:
        t2 = Table(title="分动作平均收益%")
        t2.add_column("动作")
        t2.add_column("avg%")
        for k, v in by_action.items():
            t2.add_row(str(k), str(v))
        console.print(t2)
    for m in stats.get("open_marks") or []:
        console.print(
            f"  open {m.get('code')} entry={m.get('entry')} now={m.get('current')} ret={m.get('return_pct')}%"
        )

    from money_more.analysis.factor_ic import compute_factor_ic_from_db

    ic = compute_factor_ic_from_db(db)
    console.print(Panel(f"因子 IC 诊断: {ic}", title="factor IC", style="cyan"))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from money_more.analysis.digest_compare import compare_digests, load_digests

    config = load_config(args.config)
    dig_dir = config.resolve(config.paths.reports) / "digests"
    digests = load_digests(dig_dir, limit=args.limit)
    report = compare_digests(digests)
    console.print(Panel(str(report), title="digest-compare"))
    return 0 if report.get("ok") else 1


def cmd_risk_check(args: argparse.Namespace) -> int:
    """对最近一次建议做组合风控检查。"""
    from money_more.analysis.risk_check import risk_check_book

    config = load_config(args.config)
    db = Database(config.resolve(config.paths.db))
    recs = db.get_recent_recommendations(limit=args.limit)
    # 只取最近一个交易日
    if recs:
        latest = recs[0].get("run_date")
        recs = [r for r in recs if r.get("run_date") == latest]
    report = risk_check_book(
        [
            {
                "code": r.get("stock_code"),
                "action": r.get("action"),
                "position_pct": r.get("position_pct"),
            }
            for r in recs
        ],
        max_single=config.trading.max_single_position_pct,
        max_total=config.trading.max_total_position_pct,
    )
    style = "green" if report["ok"] else "red"
    console.print(Panel(str(report), title="risk-check", style=style))
    return 0 if report["ok"] else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """环境与数据源自检，不调用 LLM。"""
    from money_more.data.as_of import filter_records_by_date
    from money_more.data.intelligence import IntelligenceFetcher

    config = load_config(args.config)
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    console.print(Panel(f"自检 as_of={run_date.isoformat()}", title="money_more doctor"))

    ok = True
    if not config.llm_api_key:
        console.print("[red]LLM_API_KEY 未配置[/red]")
        ok = False
    else:
        console.print(f"[green]LLM[/green] model={config.llm_model} base={config.llm_base_url}")

    token = (config.tushare_token or "").strip()
    if not token or token.startswith("your_"):
        console.print(
            "[yellow]TUSHARE_TOKEN 未配置或仍为占位符[/yellow] — "
            "双源估值/公告/业绩预告/解禁将不可用，数据质量会降级。请到 https://tushare.pro 获取后写入 .env"
        )
    else:
        from money_more.data.tushare_source import TushareSource

        ts = TushareSource(token, as_of=run_date)
        if ts.probe():
            console.print("[green]Tushare[/green] token OK")
        else:
            console.print(f"[red]Tushare[/red] {ts._probe_error}")
            ok = False

    fetcher = MarketDataFetcher(as_of=run_date)
    try:
        ov = fetcher.fetch_market_overview()
        console.print(
            f"[green]Market[/green] indices={len(ov.get('indices') or [])} errors={len(ov.get('errors') or [])}"
        )
    except Exception as exc:
        console.print(f"[red]Market fetch failed[/red] {exc}")
        ok = False

    intel = IntelligenceFetcher(config, as_of=run_date)
    try:
        macro = intel.fetch_macro_intelligence()
        stale = "policy_news_stale_or_empty" in (macro.get("errors") or [])
        console.print(
            f"policy_news={len(macro.get('policy_news') or [])} "
            f"rss={len(macro.get('rss_telegraph') or [])} "
            f"macro_hard={list((macro.get('macro_hard') or {}).keys())} "
            f"stale_flag={stale}"
        )
        # 演示新鲜度过滤
        demo = filter_records_by_date(
            [{"日期": "2020-01-01"}, {"日期": run_date.isoformat()}], run_date, 7
        )
        console.print(f"freshness_filter demo kept={len(demo)}")
    except Exception as exc:
        console.print(f"[red]Intelligence failed[/red] {exc}")
        ok = False

    db = Database(config.resolve(config.paths.db))
    n = db.fail_stuck_runs(max_hours=6)
    console.print(f"DB stuck runs repaired: {n}")
    console.print(Panel("OK" if ok else "有问题需处理", style="green" if ok else "red"))
    return 0 if ok else 1


def main() -> int:
    _ensure_src_on_path()

    parser = argparse.ArgumentParser(description="money_more - A股 AI 投资决策助手")
    parser.add_argument("-c", "--config", default=None, help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="执行完整分析流程（建议+复盘+趋势）")
    p_run.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD")
    p_run.add_argument("--skip-debate", action="store_true", help="跳过 Top-K 多空辩论（更快）")
    p_run.add_argument("--optimize", action="store_true", help="分析结束后调用 Cursor Agent 优化代码")
    p_run.set_defaults(func=cmd_run)

    p_sched = sub.add_parser(
        "scheduled",
        help="周期流程（默认每5天）：门禁 → 分析报告 + Cursor 自优化报告",
    )
    p_sched.add_argument("--date", default=None)
    p_sched.add_argument("--skip-debate", action="store_true")
    p_sched.add_argument("--skip-optimize", action="store_true", help="只分析，不调用 Cursor")
    p_sched.add_argument("--force", action="store_true", help="忽略间隔门禁，强制跑一轮")
    p_sched.set_defaults(func=cmd_scheduled)

    p_weekly = sub.add_parser("weekly", help="同 scheduled（兼容旧名）")
    p_weekly.add_argument("--date", default=None)
    p_weekly.add_argument("--skip-debate", action="store_true")
    p_weekly.add_argument("--skip-optimize", action="store_true", help="只分析，不调用 Cursor")
    p_weekly.add_argument("--force", action="store_true", help="忽略间隔门禁，强制跑一轮")
    p_weekly.set_defaults(func=cmd_weekly)

    p_optimize = sub.add_parser("optimize", help="仅调用 Cursor Agent 优化本仓库代码")
    p_optimize.add_argument("--date", default=None, help="写入 prompt 的报告日期")
    p_optimize.set_defaults(func=cmd_optimize)

    p_email = sub.add_parser("email-test", help="发送一封测试邮件（验证 SMTP）")
    p_email.set_defaults(func=cmd_email_test)

    p_review = sub.add_parser("review", help="仅执行复盘")
    p_review.add_argument("--date", default=None)
    p_review.set_defaults(func=cmd_review)

    p_lessons = sub.add_parser("lessons", help="查看经验库")
    p_lessons.add_argument("--limit", type=int, default=20)
    p_lessons.set_defaults(func=cmd_lessons)

    p_history = sub.add_parser("history", help="查看历史建议")
    p_history.add_argument("--limit", type=int, default=10)
    p_history.set_defaults(func=cmd_history)

    p_trend = sub.add_parser("trend", help="查看滚动趋势报告")
    p_trend.set_defaults(func=cmd_trend)

    p_stats = sub.add_parser("stats", help="纸面交易胜率/收益统计")
    p_stats.set_defaults(func=cmd_stats)

    p_doctor = sub.add_parser("doctor", help="环境与数据源自检（不调用 LLM）")
    p_doctor.add_argument("--date", default=None)
    p_doctor.set_defaults(func=cmd_doctor)

    p_risk = sub.add_parser("risk-check", help="检查最近建议的仓位/板块集中度")
    p_risk.add_argument("--limit", type=int, default=30)
    p_risk.set_defaults(func=cmd_risk_check)

    p_cmp = sub.add_parser("compare", help="对比近日 decision digest（稳定性）")
    p_cmp.add_argument("--limit", type=int, default=10)
    p_cmp.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
