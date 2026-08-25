"""Tushare Pro 数据源。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

import re

from money_more.analysis.valuation import build_valuation_percentiles
from money_more.data.as_of import parse_as_of, recent_weekdays, ymd, ymd_hms
from money_more.data.fetcher import _df_row_to_dict, _safe_float, normalize_code


def is_tushare_news_optional_error(msg: str | None) -> bool:
    """联播/个股 news 未开通 ≠ 财务估值整包不可用。勿把 major_news 算进可选新闻。"""
    text = str(msg or "")
    if "cctv_news" in text:
        return True
    if re.search(r"接口\(news\)", text):
        return True
    if re.match(r"(?is)^news\s*:", text.strip()):
        return True
    return False


def to_ts_code(code: str) -> str:
    c = normalize_code(code)
    if c.startswith(("5", "6", "9")):
        return f"{c}.SH"
    return f"{c}.SZ"


def _records(df: pd.DataFrame | None, limit: int = 10) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in df.head(limit).iterrows():
        item = _df_row_to_dict(row)
        for k, v in list(item.items()):
            if hasattr(v, "isoformat"):
                item[k] = v.isoformat()
            elif pd.isna(v) if not isinstance(v, (str, int, float, bool)) else False:
                item.pop(k, None)
        rows.append(item)
    return rows


class TushareSource:
    """Tushare Pro 封装：公告、财务、估值、新闻。"""

    def __init__(self, token: str | None, as_of: date | str | None = None) -> None:
        self.token = (token or "").strip()
        self.as_of = parse_as_of(as_of)
        self._pro = None
        self.available = bool(self.token)
        self._probe_error: str | None = None

    def set_as_of(self, as_of: date | str | None) -> None:
        self.as_of = parse_as_of(as_of)

    def probe(self) -> bool:
        """验证 token；失败时置 available=False。

        注意：勿用 trade_cal 做探测——部分套餐对该接口限频极严（如 1 次/分钟或 1 次/小时），
        探测本身就会把整轮 Tushare 误判为不可用。优先 stock_basic。
        """
        if not self.token:
            self.available = False
            self._probe_error = "未配置 TUSHARE_TOKEN"
            return False
        try:
            import tushare as ts

            ts.set_token(self.token)
            pro = ts.pro_api()
            # stock_basic 额度通常远宽于 trade_cal；limit 字段部分环境忽略，取 head 即可
            df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
            if df is None or getattr(df, "empty", True):
                raise RuntimeError("stock_basic 返回空，token 可能无效或无权限")
            self._pro = pro
            self.available = True
            self._probe_error = None
            return True
        except Exception as exc:
            msg = str(exc)
            # 频次限制 ≠ token 无效：保留 client，允许业务接口继续尝试
            if "频率超限" in msg or "频次" in msg or "每天" in msg:
                try:
                    import tushare as ts

                    ts.set_token(self.token)
                    self._pro = ts.pro_api()
                    self.available = True
                    self._probe_error = f"probe 撞限但 token 保留可用: {msg}"
                    return True
                except Exception as exc2:
                    msg = f"{msg}; 重建 client 失败: {exc2}"
            self.available = False
            self._probe_error = msg
            self._pro = None
            return False

    def _client(self):
        if not self.available:
            raise RuntimeError(self._probe_error or "未配置 TUSHARE_TOKEN")
        if self._pro is None:
            if not self.probe():
                raise RuntimeError(self._probe_error or "Tushare 鉴权失败")
        return self._pro

    def _safe_call(self, method: str, **kwargs) -> pd.DataFrame:
        pro = self._client()
        fn = getattr(pro, method)
        df = fn(**kwargs)
        if df is None:
            return pd.DataFrame()
        return df

    def fetch_macro_news(self, limit: int = 10) -> dict[str, Any]:
        result: dict[str, Any] = {"items": [], "errors": []}
        end = ymd_hms(self.as_of)
        start = ymd_hms(self.as_of, -3)
        for method, kwargs in [
            ("major_news", {"src": "新浪财经", "start_date": start, "end_date": end}),
            ("major_news", {"src": "华尔街见闻", "start_date": start, "end_date": end}),
            ("cctv_news", {"date": ymd(self.as_of)}),
        ]:
            try:
                df = self._safe_call(method, **kwargs)
                result["items"].extend(_records(df, limit))
            except Exception as exc:
                result["errors"].append(f"{method}: {exc}")
        result["items"] = result["items"][:limit]
        return result

    def fetch_forecast(self, code: str) -> dict[str, Any]:
        ts_code = to_ts_code(code)
        result: dict[str, Any] = {"items": [], "errors": []}
        try:
            df = self._safe_call(
                "forecast",
                ts_code=ts_code,
                start_date=ymd(self.as_of, -365),
                end_date=ymd(self.as_of),
            )
            result["items"] = _records(df, 10)
        except Exception as exc:
            result["errors"].append(f"forecast: {exc}")
        return result

    def fetch_share_float(self, code: str) -> dict[str, Any]:
        ts_code = to_ts_code(code)
        result: dict[str, Any] = {"items": [], "errors": []}
        try:
            df = self._safe_call(
                "share_float",
                ts_code=ts_code,
                start_date=ymd(self.as_of, -30),
                end_date=ymd(self.as_of, 90),
            )
            result["items"] = _records(df, 10)
        except Exception as exc:
            result["errors"].append(f"share_float: {exc}")
        return result

    def fetch_stock_bundle(self, code: str, limit: int = 8) -> dict[str, Any]:
        ts_code = to_ts_code(code)
        end = ymd(self.as_of)
        start = ymd(self.as_of, -30)
        start_long = ymd(self.as_of, -1100)  # ~3 年交易日，供估值分位
        result: dict[str, Any] = {
            "code": normalize_code(code),
            "ts_code": ts_code,
            "as_of": self.as_of.isoformat(),
            "announcements": [],
            "financials": {},
            "valuation": {},
            "news": [],
            "company": {},
            "forecast": [],
            "share_float": [],
            "errors": [],
        }

        for method, kwargs, key in [
            ("anns_d", {"ts_code": ts_code, "start_date": start, "end_date": end}, "announcements"),
            (
                "anns_d",
                {"ts_code": ts_code, "start_date": ymd(self.as_of, -365), "end_date": end},
                "announcements_1y",
            ),
        ]:
            try:
                df = self._safe_call(method, **kwargs)
                items = _records(df, limit)
                if key == "announcements":
                    result["announcements"] = items
                elif items:
                    result.setdefault("announcements_extended", []).extend(items)
            except Exception as exc:
                result["errors"].append(f"{method}: {exc}")

        try:
            df = self._safe_call("fina_indicator", ts_code=ts_code, limit=4)
            result["financials"]["indicators"] = _records(df, 4)
        except Exception as exc:
            result["errors"].append(f"fina_indicator: {exc}")

        for method, key in [("income", "income"), ("balancesheet", "balance"), ("cashflow", "cashflow")]:
            try:
                df = self._safe_call(method, ts_code=ts_code, limit=4)
                result["financials"][key] = _records(df, 4)
            except Exception as exc:
                result["errors"].append(f"{method}: {exc}")

        try:
            df = self._safe_call("daily_basic", ts_code=ts_code, start_date=start_long, end_date=end)
            if not df.empty:
                if "trade_date" in df.columns:
                    df = df.sort_values("trade_date", ascending=False)
                latest_row = _df_row_to_dict(df.iloc[0])
                result["valuation"]["latest"] = latest_row
                result["valuation"]["history"] = _records(df.head(5), 5)
                result["valuation"]["percentiles"] = build_valuation_percentiles(
                    df.to_dict(orient="records"),
                    latest_row,
                )
        except Exception as exc:
            result["errors"].append(f"daily_basic: {exc}")

        try:
            df = self._safe_call(
                "news",
                src="sina",
                start_date=ymd_hms(self.as_of, -7),
                end_date=ymd_hms(self.as_of),
            )
            if not df.empty and "title" in df.columns:
                code_short = normalize_code(code)
                name_cols = [c for c in df.columns if "name" in c.lower() or "名称" in c]
                if name_cols:
                    matched = df[df[name_cols[0]].astype(str).str.contains(code_short, na=False)]
                else:
                    matched = df[df["title"].astype(str).str.contains(code_short, na=False)]
                result["news"] = _records(matched if not matched.empty else df, limit)
            else:
                result["news"] = _records(df, limit)
        except Exception as exc:
            result["errors"].append(f"news: {exc}")

        try:
            df = self._safe_call("stock_company", ts_code=ts_code)
            if not df.empty:
                result["company"] = _df_row_to_dict(df.iloc[0])
        except Exception as exc:
            result["errors"].append(f"stock_company: {exc}")

        forecast = self.fetch_forecast(code)
        result["forecast"] = forecast.get("items") or []
        result["errors"].extend(forecast.get("errors") or [])

        share_float = self.fetch_share_float(code)
        result["share_float"] = share_float.get("items") or []
        result["errors"].extend(share_float.get("errors") or [])

        return result

    def fetch_sector_news(self, keyword: str, limit: int = 8) -> dict[str, Any]:
        result: dict[str, Any] = {"keyword": keyword, "items": [], "errors": []}
        end = ymd_hms(self.as_of)
        start = ymd_hms(self.as_of, -7)
        try:
            df = self._safe_call("major_news", src="新浪财经", start_date=start, end_date=end)
            if not df.empty:
                title_col = "title" if "title" in df.columns else df.columns[0]
                matched = df[df[title_col].astype(str).str.contains(keyword, na=False)]
                result["items"] = _records(matched if not matched.empty else df, limit)
        except Exception as exc:
            result["errors"].append(str(exc))
        return result

    def fetch_pledge_stat(self, code: str) -> dict[str, Any]:
        """股权质押统计（pledge_stat）；返回最新一期比例（单位 %）。"""
        ts_code = to_ts_code(code)
        result: dict[str, Any] = {"items": [], "latest": None, "errors": []}
        try:
            df = self._safe_call("pledge_stat", ts_code=ts_code)
            if df is None or df.empty:
                return result
            if "end_date" in df.columns:
                df = df.sort_values("end_date", ascending=False)
            items = _records(df, 6)
            result["items"] = items
            latest = items[0] if items else None
            if latest:
                # pledge_ratio 字段一般为百分比数值（如 4.35）
                ratio = _safe_float(latest.get("pledge_ratio") or latest.get("pledge_ratio_unrest"))
                result["latest"] = {
                    "ratio": ratio,
                    "end_date": latest.get("end_date"),
                    "pledge_count": latest.get("pledge_count"),
                    "unrest_pledge_ratio": _safe_float(latest.get("unrest_pledge_ratio")),
                    "as_of": latest.get("end_date"),
                    "source": "tushare_pledge_stat",
                    "raw": latest,
                }
        except Exception as exc:
            result["errors"].append(f"pledge_stat: {exc}")
        return result

    def fetch_holder_trades(self, code: str, lookback_days: int = 90) -> dict[str, Any]:
        """股东增减持（stk_holdertrade）；默认近 lookback_days。"""
        ts_code = to_ts_code(code)
        result: dict[str, Any] = {"items": [], "reduce_items": [], "errors": []}
        start = ymd(self.as_of, -lookback_days)
        end = ymd(self.as_of)
        try:
            df = self._safe_call(
                "stk_holdertrade",
                ts_code=ts_code,
                start_date=start,
                end_date=end,
            )
            if df is None or df.empty:
                return result
            if "ann_date" in df.columns:
                df = df.sort_values("ann_date", ascending=False)
            items = _records(df, 40)
            result["items"] = items
            result["reduce_items"] = [
                r for r in items if str(r.get("in_de") or "").upper() == "DE"
            ]
        except Exception as exc:
            result["errors"].append(f"stk_holdertrade: {exc}")
        return result

    def fetch_macro_hard(self, limit: int = 6) -> dict[str, Any]:
        """宏观硬指标：PMI/CPI/M2/社融 → 规范化序列（新→旧）。"""
        result: dict[str, Any] = {
            "pmi": [],
            "cpi": [],
            "m2": [],
            "social_financing": [],
            "errors": [],
        }
        # —— PMI：制造业 PMI010000 ——
        try:
            df = self._safe_call("cn_pmi", start_m="200001", end_m=ymd(self.as_of)[:6])
            if df is not None and not df.empty:
                month_col = "month" if "month" in df.columns else ("MONTH" if "MONTH" in df.columns else None)
                val_col = "PMI010000" if "PMI010000" in df.columns else None
                if month_col and val_col:
                    work = df[[month_col, val_col]].dropna()
                    work = work.sort_values(month_col, ascending=False)
                    rows = []
                    for _, row in work.head(limit).iterrows():
                        month = str(row[month_col]).strip().replace("-", "")[:6]
                        val = _safe_float(row[val_col])
                        rows.append(
                            {
                                "月份": month,
                                "制造业": val,
                                "value": val,
                                "label": "制造业PMI",
                                "source": "tushare_cn_pmi",
                            }
                        )
                    result["pmi"] = rows
        except Exception as exc:
            result["errors"].append(f"cn_pmi: {exc}")

        # —— CPI：全国同比 nt_yoy ——
        try:
            df = self._safe_call("cn_cpi", start_m="200001", end_m=ymd(self.as_of)[:6])
            if df is not None and not df.empty:
                month_col = "month" if "month" in df.columns else None
                if month_col:
                    work = df.sort_values(month_col, ascending=False)
                    rows = []
                    for _, row in work.head(limit).iterrows():
                        month = str(row[month_col]).strip().replace("-", "")[:6]
                        yoy = _safe_float(row["nt_yoy"]) if "nt_yoy" in work.columns else None
                        rows.append(
                            {
                                "月份": month,
                                "全国同比": yoy,
                                "value": yoy,
                                "label": "CPI同比",
                                "source": "tushare_cn_cpi",
                            }
                        )
                    result["cpi"] = rows
        except Exception as exc:
            result["errors"].append(f"cn_cpi: {exc}")

        # —— M2：m2_yoy ——
        try:
            df = self._safe_call("cn_m", start_m="200001", end_m=ymd(self.as_of)[:6])
            if df is not None and not df.empty:
                month_col = "month" if "month" in df.columns else None
                if month_col:
                    work = df.sort_values(month_col, ascending=False)
                    rows = []
                    for _, row in work.head(limit).iterrows():
                        month = str(row[month_col]).strip().replace("-", "")[:6]
                        yoy = _safe_float(row["m2_yoy"]) if "m2_yoy" in work.columns else None
                        rows.append(
                            {
                                "月份": month,
                                "M2同比": yoy,
                                "value": yoy,
                                "label": "M2同比",
                                "source": "tushare_cn_m",
                            }
                        )
                    result["m2"] = rows
        except Exception as exc:
            result["errors"].append(f"cn_m: {exc}")

        # —— 社融：sf_month.inc_month ——
        try:
            df = self._safe_call("sf_month", start_m="200001", end_m=ymd(self.as_of)[:6])
            if df is not None and not df.empty:
                month_col = "month" if "month" in df.columns else None
                if month_col:
                    work = df.sort_values(month_col, ascending=False)
                    rows = []
                    for _, row in work.head(limit).iterrows():
                        month = str(row[month_col]).strip().replace("-", "")[:6]
                        inc = _safe_float(row["inc_month"]) if "inc_month" in work.columns else None
                        rows.append(
                            {
                                "月份": month,
                                "社融增量": inc,
                                "value": inc,
                                "label": "社会融资规模增量",
                                "source": "tushare_sf_month",
                            }
                        )
                    result["social_financing"] = rows
        except Exception as exc:
            result["errors"].append(f"sf_month: {exc}")

        return result

    def fetch_margin_market(self, lookback: int = 15) -> dict[str, Any]:
        """市场两融：按日汇总沪+深融资余额，构造与 Ak margin_trend 近似结构。

        注意：部分交易日 Tushare 可能只返回 SSE（数据未齐），不可与完整日直接比变化。
        仅纳入同时含 SSE+SZSE 的交易日，余额取二者之和（不含北交所，贴近沪/深主序列）。
        """
        result: dict[str, Any] = {
            "latest": None,
            "financing_balance_change_5d_pct": None,
            "recent": [],
            "as_of": None,
            "source": "tushare_margin",
            "errors": [],
        }
        by_date: dict[str, float] = {}
        for day in recent_weekdays(self.as_of, lookback):
            try:
                df = self._safe_call("margin", trade_date=day)
            except Exception as exc:
                result["errors"].append(f"margin@{day}: {exc}")
                continue
            if df is None or df.empty or "rzye" not in df.columns:
                continue
            if "exchange_id" in df.columns:
                ex = set(str(x) for x in df["exchange_id"].tolist())
                if not {"SSE", "SZSE"}.issubset(ex):
                    continue
                work = df[df["exchange_id"].isin(["SSE", "SZSE"])]
            else:
                work = df
            total = float(pd.to_numeric(work["rzye"], errors="coerce").fillna(0).sum())
            if total > 0:
                by_date[day] = total
        if not by_date:
            return result
        dates = sorted(by_date.keys())
        recent_dates = dates[-10:]
        series = [{"trade_date": d, "融资余额": by_date[d]} for d in recent_dates]
        result["recent"] = series[-5:]
        latest = series[-1]
        result["latest"] = latest
        result["as_of"] = latest["trade_date"]
        result["trade_date"] = latest["trade_date"]
        if len(series) >= 5:
            prev = series[-5]
            cur = _safe_float(latest.get("融资余额"))
            old = _safe_float(prev.get("融资余额"))
            if cur is not None and old not in (None, 0):
                result["financing_balance_change_5d_pct"] = round((cur - old) / old * 100, 2)
        return result

    def fetch_margin_detail(self, code: str, lookback_days: int = 10) -> dict[str, Any]:
        """个股两融明细（margin_detail）。"""
        ts_code = to_ts_code(code)
        result: dict[str, Any] = {"items": [], "errors": []}
        start = ymd(self.as_of, -lookback_days)
        end = ymd(self.as_of)
        try:
            df = self._safe_call(
                "margin_detail",
                ts_code=ts_code,
                start_date=start,
                end_date=end,
            )
            if df is None or df.empty:
                return result
            if "trade_date" in df.columns:
                df = df.sort_values("trade_date", ascending=False)
            items = _records(df, 5)
            for item in items:
                item["source"] = "tushare_margin_detail"
                # 兼容中文字段名供下游阅读
                if "rzye" in item and "融资余额" not in item:
                    item["融资余额"] = item.get("rzye")
                if "rqye" in item and "融券余额" not in item:
                    item["融券余额"] = item.get("rqye")
            result["items"] = items
        except Exception as exc:
            result["errors"].append(f"margin_detail: {exc}")
        return result
