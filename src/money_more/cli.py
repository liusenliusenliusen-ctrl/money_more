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


def _run_completed(result: dict) -> bool:
    """跑完（含 LLM 降级）为 True；aborted/partial 为 False。"""
    if result.get("partial") or str(result.get("run_status") or "") == "aborted":
        return False
    return True


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.resolve(config.paths.db))
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    fetcher = MarketDataFetcher(as_of=run_date)
    llm = LLMClient(config)
    pipeline = DecisionPipeline(config, db, fetcher, llm)

    cadence = config.schedule.cadence
    label = {
        "tue_fri": "每周二/周五",
        "tuesday_friday": "每周二/周五",
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

    # pipeline.run_daily 对分析异常返回 partial/degraded result，一般不抛
    if getattr(args, "skip_debate", False):
        pipeline.config.analysis.debate_top_k = 0
    try:
        result = pipeline.run_daily(run_date)
    except Exception as exc:
        console.print(Panel(str(exc), title="运行失败", style="red"))
        result = {
            "run_date": run_date.isoformat(),
            "error": str(exc),
            "partial": True,
            "run_status": "aborted",
            "recommendations": [],
            "portfolio_summary": f"运行失败: {exc}",
            "market": {},
            "sectors": [],
            "stocks": [],
            "reviews": [],
            "trend": {},
            "intelligence": {},
            "data_quality": {
                "llm_degraded": True,
                "llm_note": f"运行异常中断，仍尝试发邮件通知。错误: {exc}",
                "note": "本轮在启动阶段中断；数据台账以已落盘内容为准",
                "degraded": False,
            },
            "multi_agent": {"enabled": False, "meta": "hard_fail", "errors": [str(exc)]},
            "llm_stage_errors": [str(exc)],
        }

    completed = _run_completed(result)
    dq = result.get("data_quality") or {}
    reports_dir = config.resolve(config.paths.reports)
    try:
        report_path = save_report(
            result,
            reports_dir,
            preserve_existing_datasources=bool(result.get("partial")),
        )
    except Exception as save_exc:
        console.print(Panel(f"报告保存失败: {save_exc}", style="red"))
        if result.get("run_id") is not None:
            db.finish_run(int(result["run_id"]), "failed")
        return 1

    if result.get("run_id") is not None:
        db.finish_run(
            int(result["run_id"]),
            "success" if completed else "failed",
            str(report_path),
        )

    console.print("\n" + render_daily_report(result))
    paths = result.get("report_paths") or {}
    if completed:
        console.print(Panel(f"主报告已保存: {report_path}", style="green"))
    else:
        console.print(
            Panel(
                f"未完整跑完，已尽量保存已采集数据与报告: {report_path}\n"
                f"run_status={result.get('run_status')} error={result.get('error')}",
                style="yellow",
            )
        )
    if paths.get("datasources"):
        console.print(Panel(f"数据源小报告: {paths['datasources']}", style="cyan"))
    if paths.get("review"):
        console.print(Panel(f"复盘小报告: {paths['review']}", style="cyan"))
    if paths.get("sim"):
        console.print(Panel(f"模拟账本小报告: {paths['sim']}", style="cyan"))
    if result.get("trend") and not (isinstance(result.get("trend"), dict) and result["trend"].get("error")):
        console.print(Panel("滚动趋势已更新: reports/trend.md", style="cyan"))
    if dq.get("llm_degraded"):
        console.print(Panel(dq.get("llm_note") or "LLM 降级", title="分析降级", style="yellow"))
    if dq.get("degraded"):
        console.print(Panel(dq.get("note", "数据降级"), title="数据质量", style="yellow"))

    if config.email.enabled and config.email.send_analysis:
        from money_more.notify import notify_analysis_report

        mail = notify_analysis_report(config, report_path, run_date.isoformat())
        if mail.get("skipped"):
            console.print(Panel(f"邮件跳过: {mail.get('reason')}", style="yellow"))
        elif mail.get("ok"):
            tag = "分析报告"
            if not completed:
                tag = "中断通知（含已采集数据）"
            elif dq.get("llm_degraded"):
                tag = "降级分析报告"
            msg = f"{tag}已发邮件 → {mail.get('to')}"
            if mail.get("guide_sent_to"):
                msg += f"\n首次附带解读文档 → {mail.get('guide_sent_to')}"
            console.print(Panel(msg, style="green" if completed else "yellow"))
        else:
            console.print(Panel(f"邮件发送失败: {mail.get('error')}", style="red"))

    return 0 if completed else 1


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
    """周期流程：按 cadence / interval_days 门禁 → 分析报告（可发邮件）。"""
    from money_more.schedule_gate import should_run, write_last_run

    config = load_config(args.config)
    force = bool(getattr(args, "force", False))
    ok, reason = should_run(
        config.project_root,
        interval_days=config.schedule.interval_days,
        force=force,
        cadence=config.schedule.cadence,
    )
    if not ok:
        console.print(Panel(reason, title="跳过本次（未到间隔）", style="yellow"))
        return 0

    console.print(Panel(reason, title="周期门禁", style="cyan"))
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
    table = Table(title="旧纸面台账（单笔标记，兼容）")
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

    # 模拟组合摘要
    _print_sim_status(config, db)

    from money_more.analysis.factor_ic import compute_factor_ic_from_db

    ic = compute_factor_ic_from_db(db)
    console.print(Panel(f"因子 IC 诊断: {ic}", title="factor IC", style="cyan"))
    return 0


def _print_sim_status(config, db) -> None:
    from money_more.sim import SimConfig, SimPortfolioEngine

    sim_cfg = getattr(config, "sim", None)
    engine = SimPortfolioEngine(
        db,
        SimConfig(
            enabled=bool(getattr(sim_cfg, "enabled", True)),
            initial_cash=float(getattr(sim_cfg, "initial_cash", 50_000)),
            lot_size=int(getattr(sim_cfg, "lot_size", 100)),
            default_buy_pct=float(getattr(sim_cfg, "default_buy_pct", 10)),
        ),
    )
    engine.ensure_account()
    summary = db.sim_summary()
    account = summary.get("account")
    latest = summary.get("latest_snapshot")
    table = Table(title="模拟组合（初始资金评估）")
    table.add_column("指标")
    table.add_column("值")
    if account:
        table.add_row("初始资金", f"{float(account['initial_cash']):,.0f}")
        table.add_row("现金", f"{float(account['cash']):,.2f}")
    if latest:
        table.add_row("最近结算日", str(latest.get("run_date")))
        eq = latest.get("equity")
        table.add_row(
            "总权益",
            f"{float(eq):,.2f}" if eq is not None else "盈亏计算失败（取价失败）",
        )
        nav = latest.get("nav_return_pct")
        table.add_row(
            "相对初始盈亏%",
            str(nav) if nav is not None else "盈亏计算失败",
        )
    table.add_row("成交笔数", str(summary.get("fill_count")))
    table.add_row("快照数", str(summary.get("snapshot_count")))
    console.print(table)
    for p in summary.get("positions") or []:
        console.print(
            f"  pos {p.get('stock_code')} shares={p.get('shares')} cost={p.get('avg_cost')}"
        )
    for s in db.sim_list_snapshots(limit=8):
        console.print(
            f"  snap {s.get('run_date')} equity={s.get('equity')} ret={s.get('nav_return_pct')}%"
        )


def cmd_sim(args: argparse.Namespace) -> int:
    """查看 / 重置模拟组合。"""
    config = load_config(args.config)
    db = Database(config.resolve(config.paths.db))
    from money_more.sim import SimConfig, SimPortfolioEngine

    sim_cfg = getattr(config, "sim", None)
    engine = SimPortfolioEngine(
        db,
        SimConfig(
            enabled=bool(getattr(sim_cfg, "enabled", True)),
            initial_cash=float(getattr(sim_cfg, "initial_cash", 50_000)),
            lot_size=int(getattr(sim_cfg, "lot_size", 100)),
            default_buy_pct=float(getattr(sim_cfg, "default_buy_pct", 10)),
        ),
    )
    if getattr(args, "reset", False):
        engine.reset()
        console.print(Panel("模拟组合已重置为初始资金（持仓与成交已清空）", style="yellow"))
    _print_sim_status(config, db)
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
    from money_more.analysis.degrade_messages import flash_chain_tip, suggest_from_err_class
    from money_more.data.ak_direct import eastmoney_bypass_mode, eastmoney_force_direct_enabled
    from money_more.data.connectivity import (
        format_doctor_table,
        probe_eastmoney_spot,
        probe_flash_news,
        probe_hist_sample,
        probe_hot_rank,
        probe_sector_flow,
    )
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

    console.print(
        f"data: force_direct={eastmoney_force_direct_enabled()} "
        f"bypass={eastmoney_bypass_mode()}"
    )
    console.print(
        f"rss: enabled={config.rss.enabled} fallback={config.rss.use_fallback_rss} "
        f"timeout={config.rss.cls_timeout_sec}s "
        f"rsshub_base={config.rss.rsshub_base or '(none)'}"
    )

    token = (config.tushare_token or "").strip()
    if not token or token.startswith("your_"):
        console.print(
            "[yellow]Tushare[/yellow] token 未配置 — 财务/公告/联播等将降级（升积分见 TODO）"
        )
    else:
        from money_more.data.tushare_source import TushareSource

        ts = TushareSource(token, as_of=run_date)
        if ts.probe():
            console.print("[green]Tushare[/green] token OK（轻量 probe，未刷受限接口）")
        else:
            console.print(f"[red]Tushare[/red] {ts._probe_error}")
            ok = False

    console.print("\n[bold]连通探测[/bold]")
    em = probe_eastmoney_spot()
    hist = probe_hist_sample("600519")
    hot = probe_hot_rank()
    sector = probe_sector_flow()
    intel = IntelligenceFetcher(config, as_of=run_date)
    flash = probe_flash_news(intel.rss)

    rows = [
        {
            "check": "spot_env",
            "ok": em["env_proxy"]["ok"],
            "ms": em["env_proxy"]["latency_ms"],
            "err": em["env_proxy"].get("err_class") or "",
            "extra": f"rows={em['env_proxy'].get('rows')}",
        },
        {
            "check": "spot_direct",
            "ok": em["force_direct"]["ok"],
            "ms": em["force_direct"]["latency_ms"],
            "err": em["force_direct"].get("err_class") or "",
            "extra": f"rows={em['force_direct'].get('rows')}",
        },
        {
            "check": "hist_sample",
            "ok": hist["ok"],
            "ms": hist["latency_ms"],
            "err": hist.get("err_class") or "",
            "extra": f"rows={hist.get('rows')}",
        },
        {
            "check": "hot_rank",
            "ok": hot["ok"],
            "ms": hot["latency_ms"],
            "err": hot.get("err_class") or "",
            "extra": f"src={hot.get('source')}",
        },
        {
            "check": "sector_flow",
            "ok": sector["ok"],
            "ms": sector["latency_ms"],
            "err": sector.get("err_class") or "",
            "extra": f"src={sector.get('source')}",
        },
        {
            "check": "flash_news",
            "ok": flash.get("ok"),
            "ms": flash.get("latency_ms"),
            "err": flash.get("err_class") or "",
            "extra": (
                f"telegraph={flash.get('telegraph')} rss={flash.get('rss_items')} "
                f"hits={flash.get('breakfast_like')}"
            ),
        },
    ]
    console.print(format_doctor_table(rows))
    console.print(f"[cyan]建议[/cyan] {em.get('tip')}")
    console.print(f"[cyan]快讯[/cyan] {flash_chain_tip()}")

    if not em["env_proxy"]["ok"] and not em["force_direct"]["ok"]:
        ok = False
    if not hist["ok"]:
        console.print("[yellow]hist 抽样失败[/yellow] — 筛股多日成交额将大量回退当日额")
    for label, probe in (("hot", hot), ("sector", sector), ("flash", flash)):
        if not probe.get("ok") and probe.get("err_class"):
            console.print(
                f"[yellow]{label}[/yellow] {suggest_from_err_class(probe.get('err_class'))}"
            )

    try:
        macro = intel.fetch_macro_intelligence()
        stale = "policy_news_stale_or_empty" in (macro.get("errors") or [])
        console.print(
            f"macro: policy_news={len(macro.get('policy_news') or [])} "
            f"rss={len(macro.get('rss_telegraph') or [])} "
            f"macro_hard={list((macro.get('macro_hard') or {}).keys())} "
            f"stale={stale}"
        )
    except Exception as exc:
        console.print(f"[red]Intelligence failed[/red] {exc}")
        ok = False

    holdings = list(config.holdings or [])
    if not holdings:
        console.print("[green]holdings[/green] 空 → 按空仓决策；深度池来自 screen")
    else:
        codes = "、".join(h.code for h in holdings[:8])
        console.print(f"[green]holdings[/green] {len(holdings)} 只: {codes}")
    screen = getattr(config, "screen", None)
    if screen:
        console.print(
            f"screen: enabled={screen.enabled} mode={screen.universe_mode} "
            f"amount_avg_days={getattr(screen, 'amount_avg_days', 0)} "
            f"max_deep={screen.max_deep}"
        )

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
    p_run.add_argument("--skip-debate", action="store_true", help="跳过 buy/add 多空辩论（更快）")
    p_run.set_defaults(func=cmd_run)

    p_sched = sub.add_parser(
        "scheduled",
        help="周期流程（默认周二/周五）：门禁 → 分析报告（可发邮件）",
    )
    p_sched.add_argument("--date", default=None)
    p_sched.add_argument("--skip-debate", action="store_true")
    p_sched.add_argument("--force", action="store_true", help="忽略间隔门禁，强制跑一轮")
    p_sched.set_defaults(func=cmd_scheduled)

    p_weekly = sub.add_parser("weekly", help="同 scheduled（兼容旧名）")
    p_weekly.add_argument("--date", default=None)
    p_weekly.add_argument("--skip-debate", action="store_true")
    p_weekly.add_argument("--force", action="store_true", help="忽略间隔门禁，强制跑一轮")
    p_weekly.set_defaults(func=cmd_weekly)

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

    p_stats = sub.add_parser("stats", help="纸面台账 + 模拟组合统计")
    p_stats.set_defaults(func=cmd_stats)

    p_sim = sub.add_parser("sim", help="查看模拟组合（--reset 清空重来）")
    p_sim.add_argument("--reset", action="store_true", help="重置为初始资金并清空持仓/成交")
    p_sim.set_defaults(func=cmd_sim)

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
