"""个股遴选漏斗：板块/全市场 → 量化初筛 → 深度分析名单。

术语：
- 声明持仓 = holdings 代码（强制纳入深度池，不占 max_deep 名额）
- 量化池 = 量化打分入围（max_quant）
- 深度池 = 声明持仓 ∪ 量化前列（最多 max_deep 只新票）

中长线约束（不推翻漏斗）：
- 打分偏估值/流动性，弱化当日涨跌幅
- 深度池按粗主题设上限，并对关注中的防御板块软保底
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd

from money_more.analysis.sector_map import infer_sector, normalize_industry, theme_bucket
from money_more.config import ScreenConfig
from money_more.data.fetcher import MarketDataFetcher, _safe_float, normalize_code
from money_more.utils.logging_util import setup_logging

log = setup_logging()

# 中长线防御主题：出现在 watch/priority 时，深度池尽量留席
_DEFENSIVE_SECTOR_KEYS = ("银行", "白酒", "医药", "保险", "食品饮料", "家电")


def run_stock_screen(
    fetcher: MarketDataFetcher,
    *,
    config: ScreenConfig,
    watch_sectors: list[str],
    force_codes: list[str] | None = None,
    must_codes: list[str] | None = None,  # 兼容旧调用名
    sector_analyses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """返回 deep_codes（供 LLM）与筛选过程摘要。

    force_codes：通常为声明持仓，强制进深度池且不占 max_deep。
    """
    force = _uniq_codes(list(force_codes if force_codes is not None else (must_codes or [])))
    if not config.enabled:
        return {
            "enabled": False,
            "ok": True,
            "deep_codes": force,
            "quant_codes": force,
            "universe_size": len(force),
            "force_codes": force,
            "must_codes": force,
            "coverage_mode": "force_only",
            "note": "screen.enabled=false，仅分析声明持仓（窄池模式）",
            "errors": [],
            "filter_stats": {},
            "plain_note": (
                "本轮未启用全市场/板块漏斗，深度分析仅覆盖声明持仓（若有）。"
                "若你期望自动筛选，请在 config.yaml 打开 screen.enabled。"
            ),
        }

    spot = fetcher._get_spot_df()
    if spot is None or spot.empty:
        log.warning("screen: spot empty, fallback to force_codes only")
        return {
            "enabled": True,
            "ok": False,
            "deep_codes": force,
            "quant_codes": force,
            "universe_size": len(force),
            "force_codes": force,
            "must_codes": force,
            "coverage_mode": "fallback_force",
            "note": "全市场行情不可用，回退声明持仓（覆盖严重不足）",
            "errors": ["spot_empty"],
            "filter_stats": {},
            "plain_note": (
                "行情接口失败，本轮无法做量化遴选，深度池只剩声明持仓（若有）。"
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
        force_df = spot[spot["code"].isin(force)]
        universe_df = pd.concat([universe_df, force_df], ignore_index=True).drop_duplicates(
            subset=["code"], keep="first"
        )

    before_filter = len(universe_df)
    universe_df, filter_stats = _apply_hard_filters(universe_df, config)
    if force:
        force_rows = spot[spot["code"].isin(force)]
        universe_df = pd.concat([universe_df, force_rows], ignore_index=True).drop_duplicates(
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

    deep, screened_added, diversify_meta = _select_deep_codes(
        quant_df,
        force=force,
        config=config,
        watch_sectors=watch_sectors,
        priority_sectors=priority_sectors,
    )
    if not deep:
        deep = quant_codes[: config.max_deep]
        screened_added = len(deep)
        diversify_meta = {
            "applied": False,
            "theme_counts": {},
            "top_theme": None,
            "top_share": 0.0,
            "floor_filled": [],
            "note": "量化池为空或深度池回退到量化前列截断",
        }

    top_rows = []
    deep_set = set(deep)
    for _, row in quant_df.head(15).iterrows():
        code = normalize_code(str(row["code"]))
        sector, theme = _row_sector_theme(row)
        top_rows.append(
            {
                "code": code,
                "name": str(row.get("name") or ""),
                "screen_score": round(float(row.get("screen_score") or 0), 2),
                "pe": _safe_float(row.get("pe")),
                "pb": _safe_float(row.get("pb")),
                "change_pct": _safe_float(row.get("change_pct")),
                "amount": _safe_float(row.get("amount")),
                "sector": sector,
                "theme": theme,
                "in_deep": code in deep_set,
                "forced": code in force,
                "must": code in force,
            }
        )

    coverage_ok = screened_added > 0 or (mode == "spot_all" and before_filter > len(force))
    force_bit = f"持仓强制{len(force)}不占名额 + " if force else ""
    force_plain = f"持仓强制 {len(force)} + " if force else ""
    theme_counts = diversify_meta.get("theme_counts") or {}
    theme_bits = "、".join(f"{k}{v}" for k, v in sorted(theme_counts.items(), key=lambda x: -x[1]))
    diversify_plain = ""
    if diversify_meta.get("applied"):
        diversify_plain = (
            f" 深度池按粗主题分散（单主题≤{config.max_deep_per_theme}"
            + (f"；分布 {theme_bits}" if theme_bits else "")
            + "）。"
        )
        if diversify_meta.get("floor_filled"):
            diversify_plain += (
                " 防御软保底："
                + "、".join(str(x) for x in diversify_meta["floor_filled"])
                + "。"
            )
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
        "force_codes": force,
        "must_codes": force,
        "quant_codes": quant_codes,
        "deep_codes": deep,
        "priority_sectors": priority_sectors,
        "sector_codes_count": len(sector_codes),
        "top_candidates": top_rows,
        "theme_concentration": {
            "theme_counts": theme_counts,
            "top_theme": diversify_meta.get("top_theme"),
            "top_share": diversify_meta.get("top_share"),
            "max_deep_per_theme": config.max_deep_per_theme if config.deep_diversify else None,
            "floor_filled": diversify_meta.get("floor_filled") or [],
            "applied": bool(diversify_meta.get("applied")),
            "note": diversify_meta.get("note") or "",
        },
        "filter_stats": filter_stats,
        "coverage_mode": "funnel",
        "errors": [],
        "note": (
            f"漏斗: {source} {before_filter}→滤后{len(universe_df)}→量化{len(quant_codes)}"
            f"→深度{len(deep)}（{force_bit}新票{screened_added}≤{config.max_deep}"
            + (f"；主题分散≤{config.max_deep_per_theme}/主题" if config.deep_diversify else "")
            + "）"
        ),
        "plain_note": (
            f"本轮从「{source}」约 {before_filter} 只候选起步，过滤后 {len(universe_df)} 只，"
            f"量化入围 {len(quant_codes)}，深度分析 {len(deep)} 只"
            f"（{force_plain}新票 {screened_added}）。"
            "深度池来自自动量化遴选"
            + ("与声明持仓" if force else "")
            + "；≠全市场逐只深挖。"
            + diversify_plain
            + "打分偏中长线（估值权重大、弱化当日涨跌）。"
        ),
    }
    if not coverage_ok and len(deep) <= max(len(force), 1):
        out["degraded"] = True
        out["ok"] = False
        out["errors"] = ["coverage_collapsed"]
        out["plain_note"] += " 警告：深度池几乎未从量化漏斗扩出，覆盖偏窄。"
    spot_source = getattr(fetcher, "spot_source", None)
    if spot_source:
        out["spot_source"] = spot_source
        if spot_source not in ("em_all", "cache"):
            out["plain_note"] += f" 行情备源={spot_source}（PE/PB 可能缺失，打分已中性处理）。"
    log.info("screen %s", out["note"])
    return out


def _row_sector_theme(row: pd.Series | dict[str, Any]) -> tuple[str | None, str]:
    if isinstance(row, dict):
        code = normalize_code(str(row.get("code") or ""))
        ind = row.get("industry")
        name = row.get("name")
    else:
        code = normalize_code(str(row.get("code") or ""))
        ind = row.get("industry") if "industry" in row.index else None
        name = row.get("name") if "name" in row.index else None
    hint = None
    if ind is not None and str(ind).strip() and str(ind).lower() not in ("nan", "none"):
        hint = str(ind)
    elif name is not None and str(name).strip():
        hint = str(name)
    sector = infer_sector(code, industry_hint=hint)
    return sector, theme_bucket(sector)


def _is_defensive_sector_name(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    return any(k in text for k in _DEFENSIVE_SECTOR_KEYS)


def _sector_matches_watch(sector: str | None, watch_name: str) -> bool:
    if not sector or not watch_name:
        return False
    s = str(sector)
    w = str(watch_name)
    if s == w or s in w or w in s:
        return True
    ns = normalize_industry(s) or s
    nw = normalize_industry(w) or w
    return ns == nw or ns in nw or nw in ns


def _select_deep_codes(
    quant_df: pd.DataFrame,
    *,
    force: list[str],
    config: ScreenConfig,
    watch_sectors: list[str],
    priority_sectors: list[str],
) -> tuple[list[str], int, dict[str, Any]]:
    """深度池：持仓强制 + 量化新票；可选主题上限与防御软保底。"""
    deep: list[str] = list(force)
    theme_counts: Counter[str] = Counter()
    floor_filled: list[str] = []

    # 持仓也计入主题占用，避免强制仓把同一主题顶满后新票全挤掉时无感知
    if not quant_df.empty:
        by_code = {normalize_code(str(r["code"])): r for _, r in quant_df.iterrows()}
    else:
        by_code = {}
    for c in force:
        row = by_code.get(c)
        if row is not None:
            _, theme = _row_sector_theme(row)
        else:
            theme = theme_bucket(infer_sector(c))
        theme_counts[theme] += 1

    screened_added = 0
    max_deep = max(0, int(config.max_deep))
    max_per = max(1, int(config.max_deep_per_theme))
    apply_div = bool(config.deep_diversify) and max_deep > 0

    def _try_add(code: str, theme: str, *, respect_cap: bool) -> bool:
        nonlocal screened_added
        if code in deep:
            return False
        if screened_added >= max_deep:
            return False
        if respect_cap and theme_counts[theme] >= max_per:
            return False
        deep.append(code)
        theme_counts[theme] += 1
        screened_added += 1
        return True

    if apply_div and max_deep > 0 and not quant_df.empty:
        floor_n = max(0, int(config.deep_theme_floor))
        floor_sources: list[str] = []
        for name in list(watch_sectors or []) + list(priority_sectors or []):
            if _is_defensive_sector_name(name) and name not in floor_sources:
                floor_sources.append(name)
        if floor_n > 0:
            for watch_name in floor_sources:
                taken = 0
                for _, row in quant_df.iterrows():
                    if taken >= floor_n or screened_added >= max_deep:
                        break
                    code = normalize_code(str(row["code"]))
                    sector, theme = _row_sector_theme(row)
                    if not _sector_matches_watch(sector, watch_name):
                        continue
                    if _try_add(code, theme, respect_cap=True):
                        taken += 1
                        label = f"{watch_name}:{code}"
                        if label not in floor_filled:
                            floor_filled.append(label)

        for _, row in quant_df.iterrows():
            if screened_added >= max_deep:
                break
            code = normalize_code(str(row["code"]))
            _, theme = _row_sector_theme(row)
            _try_add(code, theme, respect_cap=True)

        # 可选放宽：仅当显式开启；中长线默认不放宽，避免同质票填满名额
        if config.deep_relax_theme_cap and screened_added < max_deep:
            for _, row in quant_df.iterrows():
                if screened_added >= max_deep:
                    break
                code = normalize_code(str(row["code"]))
                _, theme = _row_sector_theme(row)
                _try_add(code, theme, respect_cap=False)
    else:
        for c in [normalize_code(str(x)) for x in quant_df["code"].tolist()] if not quant_df.empty else []:
            if screened_added >= max_deep:
                break
            if c in deep:
                continue
            deep.append(c)
            screened_added += 1
            row = by_code.get(c)
            if row is not None:
                _, theme = _row_sector_theme(row)
            else:
                theme = theme_bucket(infer_sector(c))
            theme_counts[theme] += 1

    # 深度池新票主题分布（不含仅强制、不在量化池的也可统计）
    new_theme_counts: Counter[str] = Counter()
    for c in deep:
        if c in force:
            continue
        row = by_code.get(c)
        if row is not None:
            _, theme = _row_sector_theme(row)
        else:
            theme = theme_bucket(infer_sector(c))
        new_theme_counts[theme] += 1
    # 报告用：整池主题（含持仓）
    report_counts = {k: int(v) for k, v in theme_counts.items() if v > 0}
    top_theme = None
    top_share = 0.0
    total = sum(report_counts.values()) or 0
    if total:
        top_theme, top_n = max(report_counts.items(), key=lambda x: x[1])
        top_share = round(top_n / total, 3)

    note_parts = []
    if apply_div:
        note_parts.append(f"单主题上限{max_per}")
        if floor_filled:
            note_parts.append(f"防御保底{len(floor_filled)}")
    meta = {
        "applied": apply_div,
        "theme_counts": report_counts,
        "new_theme_counts": {k: int(v) for k, v in new_theme_counts.items()},
        "top_theme": top_theme,
        "top_share": top_share,
        "floor_filled": floor_filled,
        "note": "；".join(note_parts) if note_parts else "未启用主题分散",
    }
    return deep, screened_added, meta


def _uniq_codes(codes: list[str]) -> list[str]:
    out: list[str] = []
    for c in codes:
        n = normalize_code(str(c))
        if n and n not in out:
            out.append(n)
    return out


def _normalize_spot(spot: pd.DataFrame) -> pd.DataFrame:
    df = spot.copy()

    def _first(*names: str) -> str | None:
        for n in names:
            if n in df.columns:
                return n
        return None

    rename: dict[str, str] = {}
    mapping = {
        "code": ("代码", "code"),
        "name": ("名称", "name"),
        "price": ("最新价", "price"),
        "change_pct": ("涨跌幅", "change_pct"),
        "amount": ("成交额", "amount"),
        "pe": ("市盈率-动态", "市盈率TTM", "市盈率-TTM", "市盈率", "pe", "PE"),
        "pb": ("市净率", "pb", "PB"),
        "mkt_cap": ("总市值", "mkt_cap"),
        "industry": ("所属行业", "行业", "industry"),
    }
    for dest, aliases in mapping.items():
        src = _first(*aliases)
        if src and src != dest:
            rename[src] = dest
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
    """中长线打分：估值权重大，成交额次之，弱化当日涨跌幅。"""
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
            0.40 * float(pe_score.iloc[i] if pe_score.iloc[i] == pe_score.iloc[i] else 50)
            + 0.25 * float(pb_score.iloc[i] if pb_score.iloc[i] == pb_score.iloc[i] else 50)
            + 0.20 * float(amt_rank.iloc[i] if amt_rank.iloc[i] == amt_rank.iloc[i] else 50)
            + 0.15 * float(chg_score.iloc[i] if chg_score.iloc[i] == chg_score.iloc[i] else 50)
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
    """中长线：偏好温和波动；暴涨降权（避免追一日热点）。"""
    v = _safe_float(chg)
    if v is None:
        return 50.0
    if v >= 9:
        return 20.0
    if v >= 5:
        return 35.0
    if v >= 2:
        return 45.0
    if v >= -1:
        return 58.0
    if v >= -4:
        return 52.0
    if v >= -8:
        return 42.0
    return 32.0


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
