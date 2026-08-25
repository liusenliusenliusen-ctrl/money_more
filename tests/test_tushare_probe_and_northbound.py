"""Tushare probe 不应依赖 trade_cal；北向持股 market 参数纠正。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from money_more.data.intelligence import IntelligenceFetcher
from money_more.data.tushare_source import TushareSource, is_tushare_news_optional_error


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
