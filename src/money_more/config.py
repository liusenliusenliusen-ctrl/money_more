from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class TradingConfig:
    max_single_position_pct: float = 20.0
    max_total_position_pct: float = 80.0
    stop_loss_pct: float = 15.0  # 中长线更宽
    take_profit_pct: float = 40.0


@dataclass
class PathsConfig:
    db: str = "data/money_more.db"
    reports: str = "reports"


@dataclass
class Holding:
    code: str
    quantity: float
    cost: float


@dataclass
class IntelligenceConfig:
    enabled: bool = True
    max_news_per_source: int = 8
    digest_before_analysis: bool = True
    news_lookback_days: int = 14  # 周期：新闻窗口更长


@dataclass
class TushareConfig:
    enabled: bool = True


@dataclass
class RssConfig:
    enabled: bool = True
    cls_direct: bool = True
    max_items_per_feed: int = 10
    use_fallback_rss: bool = False
    feeds: list[dict[str, str]] = field(default_factory=list)


@dataclass
class SentimentConfig:
    enabled: bool = True


@dataclass
class TrendConfig:
    enabled: bool = True


@dataclass
class AnalysisConfig:
    prompt_version: str = "v3-midlong-5d"
    debate_top_k: int = 1  # 周期省 token：默认只辩 Top1
    debate_min_score: float = 55.0
    investment_horizon: str = "medium_long"  # medium_long | short
    review_min_hold_days: int = 14
    paper_horizon_days: int = 60
    default_time_horizon: str = "medium"


@dataclass
class ScheduleConfig:
    cadence: str = "every_5_days"  # every_5_days | weekly | daily
    interval_days: int = 5
    run_hour: int = 1  # 本地凌晨 1 点（由 cron 触发）
    optimize_after_run: bool = True


@dataclass
class OptimizeConfig:
    enabled: bool = True
    model: str = "composer-2.5"
    max_minutes: int = 45
    # 工作区有未提交代码改动时跳过，避免覆盖人工/Cursor CLI 编辑
    skip_if_dirty: bool = True
    # 存在 logs/OPTIMIZE_PAUSE 时跳过
    respect_human_lock: bool = True


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    use_ssl: bool = True
    use_tls: bool = False
    smtp_user: str = ""
    smtp_password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)
    # 分析报告 / 自优化报告是否分别发送（默认只发分析）
    send_analysis: bool = True
    send_optimize: bool = False


@dataclass
class AgentsConfig:
    """多 Agent：主分析师 + 副分析师 + 综合委员。"""

    enabled: bool = True
    # 仅在决策环节启用双分析（省 token）；可扩展为 all
    decision_multi: bool = True
    parallel: bool = True
    primary_provider: str = "deepseek"  # openai_compat / deepseek
    primary_model: str = ""
    secondary_provider: str = "cursor"  # cursor | claude | none
    secondary_model: str = "composer-2.5"
    # 综合用 DeepSeek：便宜、JSON 稳；Cursor 更适合当独立分析师
    synthesizer_provider: str = "deepseek"
    synthesizer_model: str = ""
    cursor_model: str = "composer-2.5"


@dataclass
class AppConfig:
    watch_sectors: list[str] = field(default_factory=list)
    watch_stocks: list[str] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    trading: TradingConfig = field(default_factory=TradingConfig)
    intelligence: IntelligenceConfig = field(default_factory=IntelligenceConfig)
    tushare: TushareConfig = field(default_factory=TushareConfig)
    rss: RssConfig = field(default_factory=RssConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    trend: TrendConfig = field(default_factory=TrendConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    optimize: OptimizeConfig = field(default_factory=OptimizeConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    review_lookback_days: int = 120
    paths: PathsConfig = field(default_factory=PathsConfig)
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    tushare_token: str = ""
    cursor_api_key: str = ""
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    claude_base_url: str = ""
    project_root: Path = field(default_factory=lambda: Path.cwd())

    def resolve(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute():
            return path
        return self.project_root / path


def _normalize_code(code: str) -> str:
    digits = "".join(ch for ch in code if ch.isdigit())
    return digits[-6:].zfill(6) if digits else code


def parse_email_addrs(value: Any) -> list[str]:
    """解析收件人：支持 str（逗号/分号/空白分隔）或 list。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(parse_email_addrs(item))
        return out
    text = str(value).strip()
    if not text:
        return []
    for sep in (";", "\n", "\t", " "):
        text = text.replace(sep, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _merge_email_addrs(*sources: Any) -> list[str]:
    """按出现顺序合并去重（大小写不敏感）。"""
    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for addr in parse_email_addrs(source):
            key = addr.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(addr)
    return out


def load_config(config_path: str | Path | None = None) -> AppConfig:
    load_dotenv()
    root = Path.cwd()
    path = Path(config_path) if config_path else root / "config.yaml"
    if not path.exists():
        example = root / "config.yaml.example"
        if example.exists():
            path = example
        else:
            raise FileNotFoundError("未找到 config.yaml，请复制 config.yaml.example")

    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    trading_raw = raw.get("trading") or {}
    intel_raw = raw.get("intelligence") or {}
    tushare_raw = raw.get("tushare") or {}
    rss_raw = raw.get("rss") or {}
    sentiment_raw = raw.get("sentiment") or {}
    trend_raw = raw.get("trend") or {}
    analysis_raw = raw.get("analysis") or {}
    schedule_raw = raw.get("schedule") or {}
    optimize_raw = raw.get("optimize") or {}
    email_raw = raw.get("email") or {}
    agents_raw = raw.get("agents") or {}
    paths_raw = raw.get("paths") or {}
    holdings = [
        Holding(
            code=_normalize_code(h["code"]),
            quantity=float(h["quantity"]),
            cost=float(h["cost"]),
        )
        for h in (raw.get("holdings") or [])
    ]

    # 收件人：EMAIL_TO 支持多个（逗号/分号/空白分隔）；也可在 email.to 写列表；两者合并去重
    to_addrs = _merge_email_addrs(
        os.getenv("EMAIL_TO") or "",
        email_raw.get("to") or email_raw.get("to_addrs") or [],
    )

    smtp_port = int(os.getenv("SMTP_PORT") or email_raw.get("smtp_port") or 465)
    use_ssl_raw = email_raw.get("use_ssl")
    use_tls_raw = email_raw.get("use_tls")
    if use_ssl_raw is None and use_tls_raw is None:
        # 465 默认 SSL；587 默认 STARTTLS
        use_ssl = smtp_port == 465
        use_tls = smtp_port == 587
    else:
        use_ssl = bool(use_ssl_raw) if use_ssl_raw is not None else (smtp_port == 465)
        use_tls = bool(use_tls_raw) if use_tls_raw is not None else (not use_ssl)

    return AppConfig(
        watch_sectors=list(raw.get("watch_sectors") or []),
        watch_stocks=[_normalize_code(c) for c in (raw.get("watch_stocks") or [])],
        holdings=holdings,
        trading=TradingConfig(
            max_single_position_pct=float(trading_raw.get("max_single_position_pct", 20)),
            max_total_position_pct=float(trading_raw.get("max_total_position_pct", 80)),
            stop_loss_pct=float(trading_raw.get("stop_loss_pct", 15)),
            take_profit_pct=float(trading_raw.get("take_profit_pct", 40)),
        ),
        intelligence=IntelligenceConfig(
            enabled=bool(intel_raw.get("enabled", True)),
            max_news_per_source=int(intel_raw.get("max_news_per_source", 8)),
            digest_before_analysis=bool(intel_raw.get("digest_before_analysis", True)),
            news_lookback_days=int(intel_raw.get("news_lookback_days", 14)),
        ),
        tushare=TushareConfig(enabled=bool(tushare_raw.get("enabled", True))),
        rss=RssConfig(
            enabled=bool(rss_raw.get("enabled", True)),
            cls_direct=bool(rss_raw.get("cls_direct", True)),
            max_items_per_feed=int(rss_raw.get("max_items_per_feed", 10)),
            use_fallback_rss=bool(rss_raw.get("use_fallback_rss", False)),
            feeds=list(rss_raw.get("feeds") or []),
        ),
        sentiment=SentimentConfig(enabled=bool(sentiment_raw.get("enabled", True))),
        trend=TrendConfig(enabled=bool(trend_raw.get("enabled", True))),
        analysis=AnalysisConfig(
            prompt_version=str(analysis_raw.get("prompt_version", "v3-midlong-5d")),
            debate_top_k=int(analysis_raw.get("debate_top_k", 1)),
            debate_min_score=float(analysis_raw.get("debate_min_score", 55)),
            investment_horizon=str(analysis_raw.get("investment_horizon", "medium_long")),
            review_min_hold_days=int(analysis_raw.get("review_min_hold_days", 14)),
            paper_horizon_days=int(analysis_raw.get("paper_horizon_days", 60)),
            default_time_horizon=str(analysis_raw.get("default_time_horizon", "medium")),
        ),
        schedule=ScheduleConfig(
            cadence=str(schedule_raw.get("cadence", "every_5_days")),
            interval_days=int(schedule_raw.get("interval_days", 5)),
            run_hour=int(schedule_raw.get("run_hour", 1)),
            optimize_after_run=bool(schedule_raw.get("optimize_after_run", True)),
        ),
        optimize=OptimizeConfig(
            enabled=bool(optimize_raw.get("enabled", True)),
            model=str(optimize_raw.get("model", "composer-2.5")),
            max_minutes=int(optimize_raw.get("max_minutes", 45)),
            skip_if_dirty=bool(optimize_raw.get("skip_if_dirty", True)),
            respect_human_lock=bool(optimize_raw.get("respect_human_lock", True)),
        ),
        email=EmailConfig(
            enabled=bool(
                (os.getenv("EMAIL_ENABLED") or "").lower() in ("1", "true", "yes")
                or email_raw.get("enabled", False)
            ),
            smtp_host=str(os.getenv("SMTP_HOST") or email_raw.get("smtp_host") or ""),
            smtp_port=smtp_port,
            use_ssl=use_ssl,
            use_tls=use_tls,
            smtp_user=str(os.getenv("SMTP_USER") or email_raw.get("smtp_user") or ""),
            smtp_password=str(os.getenv("SMTP_PASSWORD") or email_raw.get("smtp_password") or ""),
            from_addr=str(os.getenv("EMAIL_FROM") or email_raw.get("from") or email_raw.get("from_addr") or ""),
            to_addrs=to_addrs,
            send_analysis=bool(email_raw.get("send_analysis", True)),
            send_optimize=bool(email_raw.get("send_optimize", False)),
        ),
        agents=AgentsConfig(
            enabled=bool(agents_raw.get("enabled", True)),
            decision_multi=bool(agents_raw.get("decision_multi", True)),
            parallel=bool(agents_raw.get("parallel", True)),
            primary_provider=str(agents_raw.get("primary_provider", "deepseek")),
            primary_model=str(agents_raw.get("primary_model") or ""),
            secondary_provider=str(agents_raw.get("secondary_provider", "cursor")),
            secondary_model=str(
                agents_raw.get("secondary_model")
                or agents_raw.get("cursor_model")
                or "composer-2.5"
            ),
            synthesizer_provider=str(agents_raw.get("synthesizer_provider", "deepseek")),
            synthesizer_model=str(agents_raw.get("synthesizer_model") or ""),
            cursor_model=str(agents_raw.get("cursor_model") or "composer-2.5"),
        ),
        review_lookback_days=int(raw.get("review_lookback_days", 120)),
        paths=PathsConfig(
            db=str(paths_raw.get("db", "data/money_more.db")),
            reports=str(paths_raw.get("reports", "reports")),
        ),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
        tushare_token=os.getenv("TUSHARE_TOKEN", ""),
        cursor_api_key=os.getenv("CURSOR_API_KEY", ""),
        claude_api_key=os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("CLAUDE_API_KEY")
        or "",
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
        claude_base_url=os.getenv("CLAUDE_BASE_URL", ""),
        project_root=root,
    )
