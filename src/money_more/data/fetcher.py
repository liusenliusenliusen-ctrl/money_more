from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd

from money_more.data.ak_direct import annotate_em_error, eastmoney_direct_session
from money_more.data.as_of import parse_as_of, ymd


def normalize_code(code: str) -> str:
    digits = "".join(ch for ch in code if ch.isdigit())
    return digits[-6:].zfill(6) if digits else code


def code_with_prefix(code: str) -> str:
    c = normalize_code(code)
    if c.startswith(("5", "6", "9")):
        return f"sh{c}"
    return f"sz{c}"


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _df_row_to_dict(row: pd.Series) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in row.items():
        if pd.isna(v):
            continue
        if hasattr(v, "item"):
            try:
                v = v.item()
            except Exception:
                pass
        result[str(k)] = v
    return result


def _match_board_name(names: pd.Series, sector_name: str) -> str | None:
    """精确匹配优先，再最长包含匹配，避免「银行」误匹配「投资银行」。"""
    clean = names.dropna().astype(str)
    exact = clean[clean == sector_name]
    if not exact.empty:
        return str(exact.iloc[0])
    contains = clean[clean.str.contains(sector_name, na=False)]
    if contains.empty:
        return None
    # 最长匹配更具体
    best = max(contains.tolist(), key=len)
    return best


def _df_records(df: pd.DataFrame | None, limit: int = 10) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return df.head(limit).to_dict(orient="records")


def _normalize_sector_summary(df: pd.DataFrame, column_map: dict[str, str]) -> pd.DataFrame:
    """将 THS/东财板块摘要统一为 板块、涨跌幅、净流入 列。"""
    out = df.copy()
    for src, dst in column_map.items():
        if src in out.columns:
            out[dst] = out[src]
    if "板块" not in out.columns:
        return pd.DataFrame()
    for col in ("涨跌幅", "净流入"):
        if col in out.columns:
            out[col] = pd.to_numeric(
                out[col].astype(str).str.replace("%", "", regex=False),
                errors="coerce",
            )
    return out.dropna(subset=["板块"]).reset_index(drop=True)


def fetch_sector_board_summary() -> tuple[pd.DataFrame, str, list[str]]:
    """板块行业摘要：THS 汇总 → THS 行业资金流 → 东财板块排名，多源回退。"""
    errors: list[str] = []
    attempts: list[tuple[str, Any, dict[str, str]]] = [
        (
            "ths_summary",
            lambda: ak.stock_board_industry_summary_ths(),
            {"板块": "板块", "涨跌幅": "涨跌幅", "净流入": "净流入"},
        ),
        (
            "ths_industry_flow",
            lambda: ak.stock_fund_flow_industry(symbol="即时"),
            {"行业": "板块", "行业-涨跌幅": "涨跌幅", "净额": "净流入"},
        ),
        (
            "em_rank",
            lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"),
            {"名称": "板块", "今日涨跌幅": "涨跌幅", "今日主力净流入-净额": "净流入"},
        ),
    ]
    for source, caller, column_map in attempts:
        try:
            if source == "em_rank":
                with eastmoney_direct_session():
                    raw = caller()
            else:
                raw = caller()
            if raw is None or raw.empty:
                errors.append(f"sector_flow_{source}_empty")
                continue
            normalized = _normalize_sector_summary(raw, column_map)
            if normalized.empty:
                errors.append(f"sector_flow_{source}_normalize_empty")
                continue
            return normalized, source, errors
        except Exception as exc:
            errors.append(annotate_em_error(f"板块资金({source})", exc))
    return pd.DataFrame(), "", errors


def build_sector_money_flow(summary: pd.DataFrame, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
    if summary.empty:
        return {}
    by_change = summary.sort_values("涨跌幅", ascending=False) if "涨跌幅" in summary.columns else summary
    payload: dict[str, list[dict[str, Any]]] = {
        "top_gainers": _df_records(by_change.head(limit), limit),
        "top_losers": _df_records(by_change.tail(limit), limit),
        "rank_by_change": _sector_rank_list(by_change, "涨跌幅", limit),
    }
    if "净流入" in summary.columns:
        by_inflow = summary.sort_values("净流入", ascending=False)
        payload["top_inflow"] = _df_records(by_inflow.head(limit), limit)
        payload["rank_by_inflow"] = _sector_rank_list(by_inflow, "净流入", limit)
    else:
        payload["top_inflow"] = []
        payload["rank_by_inflow"] = []
    return payload


def _sector_rank_list(ranked: pd.DataFrame, metric: str, limit: int) -> list[dict[str, Any]]:
    """涨跌/净流入分列排名，避免把涨幅当成资金确认。"""
    out: list[dict[str, Any]] = []
    if ranked is None or ranked.empty or "板块" not in ranked.columns:
        return out
    for i, (_, row) in enumerate(ranked.head(limit).iterrows(), start=1):
        item: dict[str, Any] = {
            "rank": i,
            "板块": row.get("板块"),
            "metric": metric,
        }
        if metric in row.index:
            item[metric] = row.get(metric)
        out.append(item)
    return out


def sector_money_flow_present(flow: Any) -> bool:
    if not isinstance(flow, dict):
        return False
    return any(isinstance(flow.get(k), list) and flow.get(k) for k in ("top_gainers", "top_losers", "top_inflow"))


def _canonicalize_spot_df(df: pd.DataFrame | None) -> pd.DataFrame:
    """统一全 A 快照：代码 6 位、去重。"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "代码" not in out.columns:
        return pd.DataFrame()
    out["代码"] = out["代码"].astype(str).map(normalize_code)
    out = out[out["代码"].str.fullmatch(r"\d{6}", na=False)]
    return out.drop_duplicates(subset=["代码"], keep="first").reset_index(drop=True)


def _fetch_em_split_spot() -> pd.DataFrame:
    """东财分市场快照（沪/深/北），全量 push2 失败时有时仍可通。"""
    parts: list[pd.DataFrame] = []
    with eastmoney_direct_session():
        for fn_name in ("stock_sh_a_spot_em", "stock_sz_a_spot_em", "stock_bj_a_spot_em"):
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            raw = fn()
            if raw is not None and not raw.empty:
                parts.append(raw)
    if not parts:
        return pd.DataFrame()
    merged = pd.concat(parts, ignore_index=True)
    return _canonicalize_spot_df(merged)


def fetch_spot_with_fallback(
    *,
    cache_key: str,
    cache: Any | None = None,
) -> tuple[pd.DataFrame, str, list[str]]:
    """全 A 现货：东财 → 东财分市场 → 新浪 → 过期磁盘缓存。"""
    from money_more.data.cache import DiskTTLCache

    errors: list[str] = []
    disk = cache if cache is not None else DiskTTLCache(Path("data/cache"), default_ttl_sec=3600)

    cached = disk.get(cache_key)
    if isinstance(cached, list) and cached:
        df = _canonicalize_spot_df(pd.DataFrame(cached))
        if not df.empty:
            return df, "cache", errors

    attempts: list[tuple[str, Any]] = [
        ("em_all", ak.stock_zh_a_spot_em),
        ("em_split", _fetch_em_split_spot),
        ("sina", ak.stock_zh_a_spot),
    ]
    for source, caller in attempts:
        try:
            if source.startswith("em"):
                with eastmoney_direct_session():
                    raw = caller()
            else:
                raw = caller()
            df = _canonicalize_spot_df(raw)
            if df.empty:
                errors.append(annotate_em_error(f"spot_{source}_empty", "empty"))
                continue
            try:
                disk.set(cache_key, df.to_dict(orient="records"), ttl_sec=3600)
            except Exception:
                pass
            if source != "em_all":
                errors.append(f"spot_fallback:{source}")
            return df, source, errors
        except Exception as exc:
            errors.append(annotate_em_error(f"spot({source})", exc))

    stale = disk.get_stale(cache_key)
    if isinstance(stale, list) and stale:
        df = _canonicalize_spot_df(pd.DataFrame(stale))
        if not df.empty:
            errors.append("spot_stale_cache")
            return df, "stale_cache", errors

    return pd.DataFrame(), "", errors


def _hot_rank_from_xueqiu_follow(follow_df: pd.DataFrame, limit: int = 100) -> pd.DataFrame:
    """雪球关注榜 → 东财人气榜近似字段（当前排名/代码/股票名称）。"""
    if follow_df is None or follow_df.empty:
        return pd.DataFrame()
    sorted_df = follow_df.sort_values("关注", ascending=False).head(limit).reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    for idx, row in sorted_df.iterrows():
        rows.append(
            {
                "当前排名": int(idx) + 1,
                "代码": row.get("股票代码"),
                "股票名称": row.get("股票简称"),
                "最新价": row.get("最新价"),
                "涨跌幅": None,
                "关注": row.get("关注"),
            }
        )
    return pd.DataFrame(rows)


def build_xueqiu_hot_snapshot(
    follow_df: pd.DataFrame,
    deal_df: pd.DataFrame,
    code: str,
) -> dict[str, Any]:
    """从已缓存的雪球关注/成交榜提取单股快照（含榜内排名）。"""
    code = normalize_code(code)
    out: dict[str, Any] = {"follow": {}, "deal": {}}

    def _rank_row(df: pd.DataFrame, label: str) -> None:
        if df is None or df.empty or "股票代码" not in df.columns:
            return
        sorted_df = df.sort_values("关注", ascending=False).reset_index(drop=True)
        for idx, row in sorted_df.iterrows():
            raw = str(row.get("股票代码") or "")
            if normalize_code(raw) != code:
                continue
            snap = _df_row_to_dict(row)
            snap["排名"] = int(idx) + 1
            snap["榜单"] = label
            out[label] = snap
            break

    _rank_row(follow_df, "follow")
    _rank_row(deal_df, "deal")
    return out


def fetch_hot_rank_with_fallback(*, limit: int = 100) -> tuple[pd.DataFrame, str, list[str]]:
    """市场人气榜：东财 push2 → 雪球关注榜（push2 失败时备源）。"""
    errors: list[str] = []
    try:
        with eastmoney_direct_session():
            raw = ak.stock_hot_rank_em()
        if raw is not None and not raw.empty:
            return raw.head(limit), "em", errors
        errors.append("hot_rank_em_empty")
    except Exception as exc:
        errors.append(annotate_em_error("hot_rank(em)", exc))

    try:
        follow = ak.stock_hot_follow_xq()
        normalized = _hot_rank_from_xueqiu_follow(follow, limit)
        if not normalized.empty:
            errors.append("hot_rank_fallback:xueqiu_follow")
            return normalized, "xueqiu_follow", errors
        errors.append("hot_rank_xueqiu_empty")
    except Exception as exc:
        errors.append(f"hot_rank(xueqiu): {exc}")

    return pd.DataFrame(), "", errors


class MarketDataFetcher:
    """从 AkShare 拉取 A 股市场数据，多数据源自动回退；支持 as_of 回放。"""

    def __init__(self, as_of: date | str | None = None) -> None:
        self.as_of = parse_as_of(as_of)
        self._spot_df: pd.DataFrame | None = None
        self._spot_error: str | None = None
        self._spot_source: str | None = None
        self._spot_warnings: list[str] = []
        self._hs300_hist: pd.DataFrame | None = None

    @property
    def spot_source(self) -> str | None:
        return self._spot_source

    def set_as_of(self, as_of: date | str | None) -> None:
        self.as_of = parse_as_of(as_of)
        self._spot_df = None
        self._spot_error = None
        self._spot_source = None
        self._spot_warnings = []
        self._hs300_hist = None

    def reset_run_cache(self) -> None:
        self._spot_df = None
        self._spot_error = None
        self._spot_source = None
        self._spot_warnings = []
        self._hs300_hist = None

    def _get_hs300_hist(self) -> pd.DataFrame:
        if self._hs300_hist is not None:
            return self._hs300_hist
        try:
            idx = ak.stock_zh_index_daily(symbol="sh000300")
            self._hs300_hist = self._slice_hist_to_as_of(idx, "date")
        except Exception:
            self._hs300_hist = pd.DataFrame()
        return self._hs300_hist

    def _get_spot_df(self) -> pd.DataFrame:
        if self._spot_df is not None:
            return self._spot_df
        if self._spot_error:
            return pd.DataFrame()
        from money_more.data.cache import DiskTTLCache

        cache = DiskTTLCache(Path("data/cache"), default_ttl_sec=3600)
        key = f"spot_em:{self.as_of.isoformat()}"
        df, source, warnings = fetch_spot_with_fallback(cache_key=key, cache=cache)
        self._spot_source = source or None
        self._spot_warnings = warnings
        if df is not None and not df.empty:
            self._spot_df = df
            return self._spot_df
        self._spot_error = "; ".join(warnings) if warnings else "spot_all_sources_failed"
        self._spot_df = pd.DataFrame()
        return self._spot_df

    def _fetch_daily_hist(self, code: str, start: str, end: str) -> pd.DataFrame:
        """优先东方财富 K 线（直连+一次重试），失败则回退新浪日线。"""
        errors: list[str] = []
        for attempt in range(2):
            try:
                with eastmoney_direct_session():
                    df = ak.stock_zh_a_hist(
                        symbol=normalize_code(code),
                        period="daily",
                        start_date=start,
                        end_date=end,
                        adjust="qfq",
                    )
                if df is not None and not df.empty:
                    return df
                errors.append(annotate_em_error(f"em_hist_empty(attempt={attempt + 1})", "empty"))
            except Exception as exc:
                errors.append(annotate_em_error(f"em_hist({attempt + 1})", exc))

        df = ak.stock_zh_a_daily(symbol=code_with_prefix(code), adjust="qfq")
        if df is None or df.empty:
            raise RuntimeError("K线获取失败: " + "; ".join(errors))
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        start_ts = pd.Timestamp(datetime.strptime(start, "%Y%m%d"))
        end_ts = pd.Timestamp(datetime.strptime(end, "%Y%m%d"))
        return df[(df["date"] >= start_ts) & (df["date"] <= end_ts)]

    def _hist_close_series(self, df: pd.DataFrame) -> pd.Series:
        if "收盘" in df.columns:
            return df["收盘"]
        return df["close"]

    def _slice_hist_to_as_of(self, df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
        if df is None or df.empty:
            return df
        col = date_col if date_col in df.columns else ("日期" if "日期" in df.columns else None)
        if not col:
            return df
        out = df.copy()
        out[col] = pd.to_datetime(out[col])
        cutoff = pd.Timestamp(self.as_of)
        sliced = out[out[col] <= cutoff]
        return sliced if not sliced.empty else out

    def fetch_market_overview(self) -> dict[str, Any]:
        overview: dict[str, Any] = {
            "as_of": self.as_of.isoformat(),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "indices": [],
            "northbound": {},
            "market_breadth": {},
            "errors": [],
        }

        index_map = {
            "上证指数": "sh000001",
            "深证成指": "sz399001",
            "创业板指": "sz399006",
            "沪深300": "sh000300",
        }
        for name, symbol in index_map.items():
            try:
                df = ak.stock_zh_index_daily(symbol=symbol)
                if df.empty:
                    continue
                df = self._slice_hist_to_as_of(df, "date")
                tail = df.tail(60).copy()
                if "close" not in tail.columns and "收盘" in tail.columns:
                    tail = tail.rename(columns={"收盘": "close", "日期": "date"})
                latest = _df_row_to_dict(tail.iloc[-1])
                prev = _df_row_to_dict(tail.iloc[-2]) if len(tail) > 1 else {}
                close = _safe_float(latest.get("close"))
                prev_close = _safe_float(prev.get("close"), close)
                change_pct = None
                if close is not None and prev_close:
                    change_pct = round((close - prev_close) / prev_close * 100, 2)
                ma20 = _safe_float(tail["close"].tail(20).mean())
                overview["indices"].append(
                    {
                        "name": name,
                        "symbol": symbol,
                        "close": close,
                        "change_pct": change_pct,
                        "ma20": ma20,
                        "above_ma20": close > ma20 if close and ma20 else None,
                        "volume": latest.get("volume"),
                    }
                )
            except Exception as exc:
                overview["errors"].append(f"指数 {name}: {exc}")

        try:
            nb = ak.stock_hsgt_hist_em(symbol="北向资金")
            if nb.empty:
                nb = ak.stock_hsgt_hist_em(symbol="沪股通")
            if not nb.empty:
                # 尽量按日期截断
                date_col = next((c for c in nb.columns if "日期" in str(c) or c == "date"), None)
                if date_col:
                    nb = nb.copy()
                    nb[date_col] = pd.to_datetime(nb[date_col], errors="coerce")
                    nb = nb[nb[date_col] <= pd.Timestamp(self.as_of)].tail(5) if not nb.empty else nb.tail(5)
                else:
                    nb = nb.tail(5)
                recent = nb.tail(5)
                latest = recent.iloc[-1]
                overview["northbound"] = {
                    "recent_days": recent.to_dict(orient="records"),
                    "latest_net": _safe_float(latest.get("当日成交净买额")),
                }
        except Exception as exc:
            overview["errors"].append(f"北向资金: {exc}")

        try:
            activity = ak.stock_market_activity_legu()
            if not activity.empty:
                overview["market_breadth"] = activity.to_dict(orient="records")
        except Exception as exc:
            overview["errors"].append(f"市场活跃度: {exc}")

        # 指数相对强弱：创业板/沪深300 近20日涨跌对比
        try:
            by_name = {x["name"]: x for x in overview["indices"]}
            cyb = by_name.get("创业板指") or {}
            hs = by_name.get("沪深300") or {}
            if cyb.get("change_pct") is not None and hs.get("change_pct") is not None:
                overview["style_proxy"] = {
                    "cyb_vs_hs300_1d": round(float(cyb["change_pct"]) - float(hs["change_pct"]), 2),
                    "note": "正值偏成长当日相对强",
                }
            # 用 close/ma20 作为趋势强度代理
            strengths = []
            for name in ("上证指数", "沪深300", "创业板指"):
                item = by_name.get(name) or {}
                if item.get("close") and item.get("ma20"):
                    strengths.append(
                        {
                            "name": name,
                            "close_over_ma20": round(float(item["close"]) / float(item["ma20"]) - 1, 4),
                        }
                    )
            if strengths:
                overview["trend_strength"] = strengths
        except Exception as exc:
            overview["errors"].append(f"相对强弱: {exc}")

        # 涨跌停家数（若接口可用）
        try:
            zt = ak.stock_zt_pool_em(date=ymd(self.as_of))
            if zt is not None and not zt.empty:
                overview["limit_up_count"] = int(len(zt))
        except Exception as exc:
            overview["errors"].append(f"涨停池: {exc}")
        try:
            dt = ak.stock_zt_pool_dtgc_em(date=ymd(self.as_of))
            if dt is not None and not dt.empty:
                overview["limit_down_count"] = int(len(dt))
        except Exception as exc:
            overview["errors"].append(f"跌停池: {exc}")

        return overview

    def list_sector_constituent_codes(self, sector_name: str, limit: int = 60) -> list[str]:
        """东财行业/概念成分代码（供筛选漏斗，不截断到 10 只）。"""
        codes: list[str] = []
        try:
            boards_em = ak.stock_board_industry_name_em()
            board_name_em = _match_board_name(boards_em["板块名称"], sector_name)
            if board_name_em:
                cons = ak.stock_board_industry_cons_em(symbol=board_name_em)
                if cons is not None and not cons.empty and "代码" in cons.columns:
                    for c in cons["代码"].astype(str).str.zfill(6).tolist()[: max(1, limit)]:
                        if c not in codes:
                            codes.append(c)
        except Exception:
            pass
        try:
            concepts = ak.stock_board_concept_name_em()
            if concepts is not None and not concepts.empty:
                name_col = "板块名称" if "板块名称" in concepts.columns else concepts.columns[0]
                matched = _match_board_name(concepts[name_col], sector_name)
                if matched:
                    cons = ak.stock_board_concept_cons_em(symbol=matched)
                    if cons is not None and not cons.empty and "代码" in cons.columns:
                        for c in cons["代码"].astype(str).str.zfill(6).tolist()[: max(1, limit // 2)]:
                            if c not in codes:
                                codes.append(c)
        except Exception:
            pass
        return codes[: max(1, limit)]

    def fetch_sector_data(self, sector_name: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "sector": sector_name,
            "as_of": self.as_of.isoformat(),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "constituents": [],
            "info": {},
            "errors": [],
        }

        try:
            boards = ak.stock_board_industry_name_ths()
            board_name = _match_board_name(boards["name"], sector_name)
            if not board_name:
                result["errors"].append(f"未找到板块: {sector_name}")
                return result

            result["board_name"] = board_name
            result["matched_board"] = board_name

            info = ak.stock_board_industry_info_ths(symbol=board_name)
            if info is not None and not info.empty:
                result["info"] = dict(zip(info["项目"], info["值"]))

            start = ymd(self.as_of, -120)
            end = ymd(self.as_of)
            hist = ak.stock_board_industry_index_ths(
                symbol=board_name, start_date=start, end_date=end
            )
            if not hist.empty:
                tail = hist.tail(20)
                latest = tail.iloc[-1]
                result["latest"] = _df_row_to_dict(latest)
                first_close = _safe_float(tail.iloc[0].get("收盘价"))
                last_close = _safe_float(latest.get("收盘价"))
                if first_close and last_close:
                    result["change_20d_pct"] = round(
                        (last_close - first_close) / first_close * 100, 2
                    )
        except Exception as exc:
            result["errors"].append(str(exc))

        try:
            boards_em = ak.stock_board_industry_name_em()
            board_name_em = _match_board_name(boards_em["板块名称"], sector_name)
            if board_name_em:
                result["matched_board_em"] = board_name_em
                cons = ak.stock_board_industry_cons_em(symbol=board_name_em)
                if not cons.empty:
                    result["constituents"] = cons.head(10).to_dict(orient="records")
        except Exception as exc:
            result["errors"].append(f"成分股(东财): {exc}")

        # 概念板块补充（主题轮动）
        try:
            concepts = ak.stock_board_concept_name_em()
            if concepts is not None and not concepts.empty:
                name_col = "板块名称" if "板块名称" in concepts.columns else concepts.columns[0]
                matched = _match_board_name(concepts[name_col], sector_name)
                if matched:
                    result["matched_concept"] = matched
                    cons = ak.stock_board_concept_cons_em(symbol=matched)
                    if cons is not None and not cons.empty:
                        result["concept_constituents"] = cons.head(8).to_dict(orient="records")
        except Exception as exc:
            result["errors"].append(f"概念板块: {exc}")

        return result

    def fetch_stock_data(self, code: str) -> dict[str, Any]:
        code = normalize_code(code)
        result: dict[str, Any] = {
            "code": code,
            "as_of": self.as_of.isoformat(),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "quote": {},
            "history": {},
            "financial": {},
            "fund_flow": {},
            "news": [],
            "errors": [],
        }

        start = ymd(self.as_of, -180)
        end = ymd(self.as_of)

        try:
            spot = self._get_spot_df()
            if spot is not None and not spot.empty:
                row = spot[spot["代码"].astype(str).map(normalize_code) == code]
                if not row.empty:
                    result["quote"] = _df_row_to_dict(row.iloc[0])
                    if self._spot_source and self._spot_source not in ("em_all", "cache"):
                        result["quote"]["_spot_source"] = self._spot_source
            elif self._spot_error:
                result["errors"].append(f"实时行情: {self._spot_error}")
        except Exception as exc:
            result["errors"].append(f"实时行情: {exc}")

        try:
            hist = self._fetch_daily_hist(code, start, end)
            hist = self._slice_hist_to_as_of(hist)
            tail = hist.tail(30)
            latest = tail.iloc[-1]
            closes = self._hist_close_series(tail)
            close = _safe_float(latest.get("收盘", latest.get("close")))
            prev_close = _safe_float(tail.iloc[-2].get("收盘", tail.iloc[-2].get("close")), close)
            change_pct = None
            if close and prev_close:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
            ma5 = _safe_float(closes.tail(5).mean())
            ma20 = _safe_float(closes.tail(20).mean())
            high_col = "最高" if "最高" in tail.columns else "high"
            low_col = "最低" if "最低" in tail.columns else "low"
            vol_col = "成交量" if "成交量" in tail.columns else "volume"
            # 近似 ATR%：20 日高低振幅均值 / close
            atr_pct = None
            if close and high_col in tail.columns and low_col in tail.columns:
                rng = (tail[high_col].astype(float) - tail[low_col].astype(float)).tail(20).mean()
                atr_pct = round(float(rng) / close * 100, 2) if close else None
            result["history"] = {
                "close": close,
                "change_pct": change_pct,
                "volume": _safe_float(latest.get(vol_col)),
                "ma5": ma5,
                "ma20": ma20,
                "above_ma20": close > ma20 if close and ma20 else None,
                "high_20d": _safe_float(tail[high_col].max()),
                "low_20d": _safe_float(tail[low_col].min()),
                "atr_pct_20d": atr_pct,
            }
            if not result["quote"]:
                result["quote"] = {"最新价": close, "代码": code}

            # 相对沪深300：用个股 20 日涨跌 - 指数 20 日涨跌（若可算）
            try:
                if close and len(tail) >= 20:
                    first = _safe_float(tail.iloc[0].get("收盘", tail.iloc[0].get("close")))
                    if first:
                        stock_20d = (close - first) / first * 100
                        idx = self._get_hs300_hist()
                        if idx is not None and len(idx) >= 20:
                            icol = "close" if "close" in idx.columns else "收盘"
                            i_tail = idx.tail(20)
                            i0 = _safe_float(i_tail.iloc[0][icol])
                            i1 = _safe_float(i_tail.iloc[-1][icol])
                            if i0 and i1:
                                idx_20d = (i1 - i0) / i0 * 100
                                result["history"]["return_20d_pct"] = round(stock_20d, 2)
                                result["history"]["rs_vs_hs300_20d"] = round(stock_20d - idx_20d, 2)
            except Exception as exc:
                result["errors"].append(f"相对强弱: {exc}")
        except Exception as exc:
            result["errors"].append(f"历史K线: {exc}")

        for label, fn, kwargs in [
            ("财务指标", ak.stock_financial_analysis_indicator, {"symbol": code}),
            ("财务摘要", ak.stock_financial_abstract, {"symbol": code}),
        ]:
            try:
                data = fn(**kwargs)
                if data is not None and not data.empty:
                    key = "indicators" if label == "财务指标" else "abstract"
                    result["financial"][key] = data.head(8).to_dict(orient="records")
            except Exception as exc:
                result["errors"].append(f"{label}: {exc}")

        try:
            news = ak.stock_news_em(symbol=code)
            if news is not None and not news.empty:
                result["news"] = news.head(5).to_dict(orient="records")
        except Exception as exc:
            result["errors"].append(f"新闻: {exc}")

        # 个股资金流向（东财；直连 + 一次重试）
        flow_errs: list[str] = []
        for attempt in range(2):
            try:
                with eastmoney_direct_session():
                    flow = ak.stock_individual_fund_flow(
                        stock=code,
                        market="sh" if code.startswith(("5", "6", "9")) else "sz",
                    )
                if flow is not None and not flow.empty:
                    flow = flow.copy()
                    date_col = "日期" if "日期" in flow.columns else None
                    if date_col:
                        flow[date_col] = pd.to_datetime(flow[date_col], errors="coerce")
                        flow = flow[flow[date_col] <= pd.Timestamp(self.as_of)]
                    tail = flow.tail(20)
                    net_col = next(
                        (
                            c
                            for c in ("主力净流入-净额", "今日主力净流入-净额", "净额")
                            if c in tail.columns
                        ),
                        None,
                    )
                    if net_col:
                        nets = pd.to_numeric(tail[net_col], errors="coerce")
                        result["fund_flow"] = {
                            "net_3d": _safe_float(nets.tail(3).sum()),
                            "net_5d": _safe_float(nets.tail(5).sum()),
                            "net_20d": _safe_float(nets.tail(20).sum()),
                            "recent": tail.tail(5).to_dict(orient="records"),
                        }
                    break
                flow_errs.append(f"empty(attempt={attempt + 1})")
            except Exception as exc:
                flow_errs.append(str(exc))
        else:
            result["errors"].append("资金流向: " + "; ".join(flow_errs) if flow_errs else "资金流向: unavailable")

        return result

    def fetch_price_on_date(self, code: str, run_date: str) -> float | None:
        code = normalize_code(code)
        try:
            target = datetime.strptime(run_date, "%Y-%m-%d")
            start = (target - timedelta(days=15)).strftime("%Y%m%d")
            end = (target + timedelta(days=5)).strftime("%Y%m%d")
            hist = self._fetch_daily_hist(code, start, end)
            date_col = "日期" if "日期" in hist.columns else "date"
            hist[date_col] = pd.to_datetime(hist[date_col])
            target_ts = pd.Timestamp(target.date())
            row = hist[hist[date_col] >= target_ts].head(1)
            if row.empty:
                row = hist.tail(1)
            return _safe_float(row.iloc[0].get("收盘", row.iloc[0].get("close")))
        except Exception:
            return None

    def fetch_current_price(self, code: str) -> float | None:
        code = normalize_code(code)
        try:
            spot = self._get_spot_df()
            if spot is not None and not spot.empty:
                row = spot[spot["代码"].astype(str).map(normalize_code) == code]
                if not row.empty:
                    return _safe_float(row.iloc[0].get("最新价"))
        except Exception:
            pass
        data = self.fetch_stock_data(code)
        quote = data.get("quote") or {}
        for key in ("最新价", "收盘", "close"):
            val = _safe_float(quote.get(key))
            if val is not None:
                return val
        hist = data.get("history") or {}
        return _safe_float(hist.get("close"))
