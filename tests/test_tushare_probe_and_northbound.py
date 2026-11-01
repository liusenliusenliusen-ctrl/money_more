"""Tushare probe 不应依赖 trade_cal；北向持股 market 参数纠正。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from money_more.data.intelligence import IntelligenceFetcher
from money_more.data.tushare_source import (
    TushareSource,
    cn_pmi_rows,
    is_tushare_news_optional_error,
    public_errors_sample,
)


def test_tushare_probe_uses_stock_basic_not_trade_cal() -> None:
    ts = TushareSource("fake_token_xxxxxx", as_of=date(2026, 8, 2))
    fake_pro = MagicMock()
    fake_pro.stock_basic.return_value = pd.DataFrame([{"ts_code": "600519.SH", "name": "贵州茅台"}])

    with patch("tushare.set_token"), patch("tushare.pro_api", return_value=fake_pro):
        assert ts.probe() is True
    fake_pro.stock_basic.assert_called()
    fake_pro.trade_cal.assert_not_called()
    assert ts.available is True


def test_tushare_probe_rate_limit_keeps_available() -> None:
    ts = TushareSource("fake_token_xxxxxx", as_of=date(2026, 8, 2))
    fake_pro = MagicMock()
    fake_pro.stock_basic.side_effect = Exception(
        "抱歉，您访问接口(stock_basic)频率超限(1次/小时)，具体频次详情：https://tushare.pro/document/1?doc_id=108。"
    )

    with patch("tushare.set_token"), patch("tushare.pro_api", return_value=fake_pro):
        assert ts.probe() is True
    assert ts.available is True
    assert ts._probe_error and "撞限" in ts._probe_error


def test_northbound_hold_lookup_from_cache() -> None:
    from money_more.config import load_config
    from money_more.data.intelligence import _filter_df
    from money_more.data.fetcher import _df_row_to_dict

    cfg = load_config()
    intel = IntelligenceFetcher(cfg, as_of=date(2026, 8, 2))
    intel._northbound_hold_df = pd.DataFrame(
        [{"代码": "600519", "名称": "贵州茅台", "今日持股-股数": 1e6}]
    )
    df = intel._get_northbound_hold_df()
    matched = _filter_df(df, "600519", ("代码", "股票代码"))
    assert not matched.empty
    assert _df_row_to_dict(matched.iloc[0])["名称"] == "贵州茅台"


def test_northbound_hold_wrong_market_documented_in_helper() -> None:
    """_get_northbound_hold_df 只传合法 market，且吞掉 AkShare 裸 TypeError。"""
    from money_more.config import load_config

    cfg = load_config()
    intel = IntelligenceFetcher(cfg, as_of=date(2026, 8, 2))
    calls: list[tuple[str, str]] = []

    def fake_hold(*, market: str = "沪股通", indicator: str = "5日排行"):
        calls.append((market, indicator))
        raise TypeError("'NoneType' object is not subscriptable")

    with patch("money_more.data.intelligence.ak.stock_hsgt_hold_stock_em", side_effect=fake_hold):
        df = intel._get_northbound_hold_df()
    assert df.empty
    assert all(m in ("北向", "沪股通", "深股通") for m, _ in calls)
    assert "北向持股" not in {m for m, _ in calls}
    assert intel._northbound_hold_error
    assert "null_result" in intel._northbound_hold_error or "NoneType" in intel._northbound_hold_error


def test_tushare_news_optional_error_helper() -> None:
    assert is_tushare_news_optional_error("cctv_news: 抱歉，您没有接口(cctv_news)访问权限")
    assert is_tushare_news_optional_error("抱歉，您没有接口(news)访问权限")
    assert is_tushare_news_optional_error("news: 抱歉，您没有接口(news)访问权限")
    assert not is_tushare_news_optional_error("major_news: 抱歉，您没有接口(major_news)访问权限")
    assert not is_tushare_news_optional_error("fina_indicator: 抱歉，您没有接口(fina_indicator)访问权限")
    assert not is_tushare_news_optional_error("Tushare 没有接口权限")


def test_public_errors_sample_drops_optional_news() -> None:
    sample = public_errors_sample(
        [
            "cctv_news: 抱歉，您没有接口(cctv_news)访问权限",
            "news: 抱歉，您没有接口(news)访问权限",
            "economic_calendar_primary_empty_used_alt",
            "macro_hard_social_financing_conflict:gap=4",
        ]
    )
    assert sample[0] == "economic_calendar_primary_empty_used_alt"
    assert all("cctv_news" not in e and not e.startswith("news:") for e in sample)


def test_cn_pmi_rows_accept_lowercase_column() -> None:
    df = pd.DataFrame(
        [
            {"month": "202608", "pmi010000": 49.8},
            {"month": "202609", "pmi010000": 50.1},
        ]
    )
    rows = cn_pmi_rows(df, limit=2)
    assert rows[0]["月份"] == "202609"
    assert rows[0]["value"] == 50.1
    assert rows[0]["制造业"] == 50.1


def test_fetch_macro_hard_retries_when_pmi_fields_mismatch() -> None:
    ts = TushareSource("fake_token_xxxxxx", as_of=date(2026, 10, 30))
    df = pd.DataFrame([{"month": "202609", "pmi010000": 50.1}])
    calls: list[dict] = []

    def _call(method: str, **kwargs):
        calls.append({"method": method, **kwargs})
        if method == "cn_pmi" and "fields" not in kwargs:
            raise RuntimeError("查询数据失败，指定了返回列，但是列名不匹配，[]")
        if method == "cn_pmi":
            return df
        return pd.DataFrame()

    ts._safe_call = _call  # type: ignore[method-assign]
    out = ts.fetch_macro_hard(limit=3)
    assert out["pmi"][0]["value"] == 50.1
    assert out["errors"] == []
    pmi_calls = [c for c in calls if c.get("method") == "cn_pmi"]
    assert pmi_calls[1].get("fields") == "month,pmi010000"


def test_trend_drops_claim_of_no_new_analysis() -> None:
    from money_more.analysis.trend import TrendReportBuilder

    out = TrendReportBuilder._merge_llm_into_deterministic(
        {
            "market_regime": {"current_phase": "range", "change_note": "舆情分 48→50"},
            "executive_summary": "",
            "market_series": [{"date": "2026-10-30"}],
        },
        {
            "as_of": "2026-10-30",
            "market_regime": {"change_note": "无新增今日分析数据，维持前日判断"},
            "executive_summary": "今日无新增分析数据，维持前一日判断。下一步关注信用数据。",
            "market_series": [{"date": "2026-10-30"}],
        },
        "2026-10-30",
    )
    assert "无新增" not in out["executive_summary"]
    assert "信用数据" in out["executive_summary"]
    assert out["market_regime"]["change_note"] == "舆情分 48→50"
