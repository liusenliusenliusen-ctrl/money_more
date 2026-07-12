"""Tushare Pro 数据源。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from money_more.analysis.valuation import build_valuation_percentiles
from money_more.data.as_of import parse_as_of, ymd, ymd_hms
from money_more.data.fetcher import _df_row_to_dict, normalize_code


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
        """验证 token；失败时置 available=False。"""
        if not self.token:
            self.available = False
            self._probe_error = "未配置 TUSHARE_TOKEN"
            return False
        try:
            import tushare as ts

            ts.set_token(self.token)
            pro = ts.pro_api()
            pro.trade_cal(
                exchange="SSE",
                start_date=ymd(self.as_of),
                end_date=ymd(self.as_of),
            )
            self._pro = pro
            self.available = True
            self._probe_error = None
            return True
        except Exception as exc:
            self.available = False
            self._probe_error = str(exc)
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
