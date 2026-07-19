"""个股遴选漏斗：板块/全市场 → 量化初筛 → 深度分析名单。

术语：
- 必跟名单 = watch_stocks + 声明持仓（强制纳入深度池，不占 max_deep 名额）
- 量化池 = 量化打分入围（max_quant）
- 深度池 = 必跟 ∪ 量化前列（最多 max_deep 只新票）
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from money_more.config import ScreenConfig
from money_more.data.fetcher import MarketDataFetcher, _safe_float, normalize_code
from money_more.utils.logging_util import setup_logging

log = setup_logging()


def run_stock_screen(
    fetcher: MarketDataFetcher,
    *,
    config: ScreenConfig,
    watch_sectors: list[str],
    must_codes: list[str],
    sector_analyses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """返回 deep_codes（供 LLM）与筛选过程摘要。"""
    must = _uniq_codes(must_codes)
    if not config.enabled:
        return {
            "enabled": False,
            "ok": True,
            "deep_codes": must,
            "quant_codes": must,
            "universe_size": len(must),
            "must_codes": must,
            "coverage_mode": "must_only",
            "note": "screen.enabled=false，仅分析必跟名单（窄池模式）",
            "errors": [],
            "filter_stats": {},
            "plain_note": (
                "本轮未启用全市场/板块漏斗，深度分析仅覆盖必跟名单。"
                "若你期望更大覆盖面，请在 config.yaml 打开 screen.enabled。"
            ),
        }

    spot = fetcher._get_spot_df()
    if spot is None or spot.empty:
        log.warning("screen: spot empty, fallback to must_codes only")
        return {
            "enabled": True,
            "ok": False,
            "deep_codes": must,
            "quant_codes": must,
            "universe_size": len(must),
            "must_codes": must,
            "coverage_mode": "fallback_must",
            "note": "全市场行情不可用，回退必跟名单（覆盖严重不足）",
            "errors": ["spot_empty"],
            "filter_stats": {},
            "plain_note": (
                "行情接口失败，本轮无法做量化遴选，深度池只剩必跟名单。"
                "结论可信度应下调，不宜当作「已全市场海选」。"
            ),
            "degraded": True,
        }

    spot = _normalize_spot(spot)
    priority_sectors = _priority_sector_names(sector_analyses)
    sector_codes = _collect_sector_codes(
        fetcher,
        watch_sectors,
        limit_per_sector=config.sector_cons_limit,
    )

    mode = (config.universe_mode or "sector_spot").strip().lower()
    if mode == "spot_all":
        universe_df = spot.copy()
        source = "spot_all"
    else:
        if sector_codes:
            universe_df = spot[spot["code"].isin(sector_codes)].copy()
            source = "sector_constituents"
        else:
            universe_df = spot.copy()
            source = "spot_all_fallback"
        must_df = spot[spot["code"].isin(must)]
        universe_df = pd.concat([universe_df, must_df], ignore_index=True).drop_duplicates(
            subset=["code"], keep="first"
        )

    before_filter = len(universe_df)
    universe_df, filter_stats = _apply_hard_filters(universe_df, config)
    if must:
        must_rows = spot[spot["code"].isin(must)]
        universe_df = pd.concat([universe_df, must_rows], ignore_index=True).drop_duplicates(
            subset=["code"], keep="first"
        )

    if len(universe_df) > config.max_universe:
        universe_df = universe_df.nlargest(config.max_universe, "amount", keep="all")

    universe_df = _score_universe(universe_df, priority_sectors, config.sector_priority_boost)
    if priority_sectors and config.sector_priority_boost:
        boost_codes: set[str] = set()
        for name in priority_sectors:
            boost_codes.update(
                fetcher.list_sector_constituent_codes(name, limit=config.sector_cons_limit)
            )
        if boost_codes:
            universe_df["screen_score"] = universe_df.apply(
                lambda r: float(r["screen_score"])
                + (config.sector_priority_boost if r["code"] in boost_codes else 0.0),
                axis=1,
            )
    universe_df = universe_df.sort_values("screen_score", ascending=False)

    quant_df = universe_df.head(config.max_quant)
    quant_codes = [normalize_code(c) for c in quant_df["code"].tolist()]

    # 深度名单：必跟不占 max_deep；另从量化池最多再取 max_deep 只
    deep: list[str] = list(must)
    screened_added = 0
    for c in quant_codes:
        if c in deep:
            continue
        deep.append(c)
        screened_added += 1
        if screened_added >= config.max_deep:
            break
    if not deep:
        deep = quant_codes[: config.max_deep]

    top_rows = []
    for _, row in quant_df.head(15).iterrows():
        top_rows.append(
            {
                "code": normalize_code(str(row["code"])),
                "name": str(row.get("name") or ""),
                "screen_score": round(float(row.get("screen_score") or 0), 2),
                "pe": _safe_float(row.get("pe")),
                "pb": _safe_float(row.get("pb")),
                "change_pct": _safe_float(row.get("change_pct")),
                "amount": _safe_float(row.get("amount")),
                "must": normalize_code(str(row["code"])) in must,
            }
        )

    coverage_ok = screened_added > 0 or (mode == "spot_all" and before_filter > len(must))
    out = {
        "enabled": True,
        "ok": True,
        "degraded": False,
        "universe_mode": mode,
        "universe_source": source,
        "universe_size_raw": before_filter,
        "universe_size": int(len(universe_df)),
        "quant_size": len(quant_codes),
        "deep_size": len(deep),
        "screened_added": screened_added,
        "must_codes": must,
        "quant_codes": quant_codes,
        "deep_codes": deep,
        "priority_sectors": priority_sectors,
        "sector_codes_count": len(sector_codes),
        "top_candidates": top_rows,
        "filter_stats": filter_stats,
        "coverage_mode": "funnel",
        "errors": [],
        "note": (
            f"漏斗: {source} {before_filter}→滤后{len(universe_df)}→量化{len(quant_codes)}"
            f"→深度{len(deep)}（必跟{len(must)}不占名额 + 新票{screened_added}≤{config.max_deep}）"
        ),
        "plain_note": (
            f"本轮从「{source}」约 {before_filter} 只候选起步，过滤后 {len(universe_df)} 只，"
            f"量化入围 {len(quant_codes)}，深度分析 {len(deep)} 只"
            f"（必跟 {len(must)} + 新票 {screened_added}）。"
            "必跟≠持仓；深度池≠全市场逐只深挖。"
        ),
    }
    if not coverage_ok and len(deep) <= max(len(must), 1):
        out["degraded"] = True
        out["ok"] = False
        out["errors"] = ["coverage_collapsed"]
        out["plain_note"] += " 警告：深度池几乎未扩出必跟，覆盖偏窄。"
    spot_source = getattr(fetcher, "spot_source", None)
    if spot_source and spot_source not in ("em_all", "cache"):
        out["spot_source"] = spot_source
        out["plain_note"] += f" 行情备源={spot_source}（PE/PB 可能缺失，打分已中性处理）。"
    log.info("screen %s", out["note"])
    return out


def _uniq_codes(codes: list[str]) -> list[str]:
    out: list[str] = []
    for c in codes:
        n = normalize_code(str(c))
        if n and n not in out:
            out.append(n)
    return out


def _normalize_spot(spot: pd.DataFrame) -> pd.DataFrame:
    df = spot.copy()
    rename = {}
    for a, b in (
        ("代码", "code"),
        ("名称", "name"),
        ("最新价", "price"),
        ("涨跌幅", "change_pct"),
        ("成交额", "amount"),
        ("市盈率-动态", "pe"),
        ("市净率", "pb"),
        ("总市值", "mkt_cap"),
        ("所属行业", "industry"),
    ):
        if a in df.columns:
            rename[a] = b
    df = df.rename(columns=rename)
    if "code" not in df.columns:
        raise ValueError("spot 缺少代码列")
    df["code"] = df["code"].astype(str).str.zfill(6)
    for col in ("price", "change_pct", "amount", "pe", "pb", "mkt_cap"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _apply_hard_filters(df: pd.DataFrame, config: ScreenConfig) -> tuple[pd.DataFrame, dict[str, int]]:
    """硬过滤；PE 默认只做软降权（见打分），除非显式配置 pe_max/exclude_negative_pe。"""
    stats = {"st": 0, "illiquid": 0, "neg_pe": 0, "high_pe": 0, "bad_price": 0}
    out = df
    n0 = len(out)
    if config.exclude_st and "name" in out.columns:
        names = out["name"].astype(str)
        mask = ~names.str.contains(r"ST|\*ST|退", regex=True, na=False)
        stats["st"] = int((~mask).sum())
        out = out[mask]
    if "amount" in out.columns and config.min_amount > 0:
        mask = out["amount"].fillna(0) >= config.min_amount
        stats["illiquid"] = int((~mask).sum())
        out = out[mask]
    if "pe" in out.columns:
        pe = out["pe"]
        if config.exclude_negative_pe:
            mask = pe.isna() | (pe > 0)
            stats["neg_pe"] = int((~mask).sum())
            out = out[mask]
            pe = out["pe"]
        # pe_max<=0 表示不硬截断（成长/高估值票可进池，由打分降权）
        if config.pe_max and config.pe_max > 0:
            mask = pe.isna() | (pe <= config.pe_max)
            stats["high_pe"] = int((~mask).sum())
            out = out[mask]
    if "price" in out.columns:
        mask = out["price"].fillna(0) > 0
        stats["bad_price"] = int((~mask).sum())
        out = out[mask]
    stats["kept"] = int(len(out))
    stats["removed"] = int(n0 - len(out))
    return out, stats


def _score_universe(
    df: pd.DataFrame,
    priority_sectors: list[str],
    sector_boost: float,
) -> pd.DataFrame:
    out = df.copy()
    scores: list[float] = []
    pe = out["pe"] if "pe" in out.columns else pd.Series([None] * len(out))
    pb = out["pb"] if "pb" in out.columns else pd.Series([None] * len(out))
    amt = out["amount"] if "amount" in out.columns else pd.Series([0.0] * len(out))
    chg = out["change_pct"] if "change_pct" in out.columns else pd.Series([0.0] * len(out))

    pe_score = pe.map(_pe_to_score)
    pb_score = pb.map(_pb_to_score)
    amt_rank = amt.rank(pct=True, method="average").fillna(0.5) * 100
    chg_score = chg.map(_chg_to_score)

    for i in range(len(out)):
        s = (
            0.35 * float(pe_score.iloc[i] if pe_score.iloc[i] == pe_score.iloc[i] else 50)
            + 0.20 * float(pb_score.iloc[i] if pb_score.iloc[i] == pb_score.iloc[i] else 50)
            + 0.25 * float(amt_rank.iloc[i] if amt_rank.iloc[i] == amt_rank.iloc[i] else 50)
            + 0.20 * float(chg_score.iloc[i] if chg_score.iloc[i] == chg_score.iloc[i] else 50)
        )
        scores.append(round(s, 3))
    out["screen_score"] = scores
    if priority_sectors and "name" in out.columns:
        _ = sector_boost
    return out


def _pe_to_score(pe: Any) -> float:
    """分桶：低估值加分；负 PE（亏损扩张）给中性偏弱而非清零；超高 PE 降权但不归零。"""
    v = _safe_float(pe)
    if v is None:
        return 50.0
    if v <= 0:
        return 48.0  # 成长/亏损期：可进池，不系统性出局
    if v < 12:
        return 90.0
    if v < 20:
        return 75.0
    if v < 35:
        return 60.0
    if v < 60:
        return 45.0
    if v < 100:
        return 35.0
    return 28.0


def _pb_to_score(pb: Any) -> float:
    v = _safe_float(pb)
    if v is None or v <= 0:
        return 50.0
    if v < 1.0:
        return 85.0
    if v < 2.0:
        return 70.0
    if v < 4.0:
        return 55.0
    if v < 8.0:
        return 40.0
    return 25.0


def _chg_to_score(chg: Any) -> float:
    v = _safe_float(chg)
    if v is None:
        return 50.0
    if v >= 9:
        return 25.0
    if v >= 5:
        return 40.0
    if v >= 0:
        return 60.0
    if v >= -3:
        return 55.0
    if v >= -7:
        return 45.0
    return 35.0


def _priority_sector_names(sector_analyses: list[dict[str, Any]] | None) -> list[str]:
    if not sector_analyses:
        return []
    out: list[str] = []
    for sec in sector_analyses:
        a = sec.get("analysis") or {}
        pri = str(a.get("priority") or "").lower()
        name = str(a.get("sector") or sec.get("sector") or "")
        if name and pri in ("high", "medium", "左侧", "高"):
            out.append(name)
    return out


def _collect_sector_codes(
    fetcher: MarketDataFetcher,
    sectors: list[str],
    *,
    limit_per_sector: int,
) -> set[str]:
    codes: set[str] = set()
    for sector in sectors:
        try:
            got = fetcher.list_sector_constituent_codes(sector, limit=limit_per_sector)
            codes.update(got)
        except Exception as exc:
            log.warning("screen sector cons failed %s: %s", sector, exc)
    return codes
