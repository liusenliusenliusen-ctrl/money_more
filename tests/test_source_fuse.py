"""双源融合：质押 / 减持 / 宏观 / 两融。"""

from __future__ import annotations

from datetime import date

from money_more.data.as_of import parse_macro_period_date
from money_more.data.source_fuse import (
    fuse_macro_series,
    fuse_margin_detail,
    fuse_margin_trend,
    fuse_pledge,
    fuse_share_reduce,
    map_ts_holder_trade_to_reduce,
)


def test_parse_macro_period_yyyymm() -> None:
    assert parse_macro_period_date({"月份": "202607"}) == date(2026, 7, 1)


def test_fuse_pledge_takes_max_and_flags_conflict() -> None:
    fused = fuse_pledge(
        {"ratio": 4.0, "trade_date": "20260807", "industry": "白酒"},
        {"ratio": 4.35, "end_date": "20260807", "source": "tushare_pledge_stat"},
    )
    assert fused is not None
    assert fused["ratio"] == 4.35
    assert fused["agreement"] == "match"
    assert set(fused["sources"]) == {"akshare", "tushare"}

    conflict = fuse_pledge({"ratio": 10.0}, {"ratio": 40.0})
    assert conflict is not None
    assert conflict["ratio"] == 40.0
    assert conflict["agreement"] == "conflict"


def test_fuse_pledge_single_source() -> None:
    only_ts = fuse_pledge(None, {"ratio": 0.48, "end_date": "20260801"})
    assert only_ts is not None
    assert only_ts["ratio"] == 0.48
    assert only_ts["agreement"] == "single"
    assert only_ts["source"] == "tushare"


def test_map_and_fuse_share_reduce() -> None:
    mapped = map_ts_holder_trade_to_reduce(
        {
            "in_de": "DE",
            "holder_name": "张三",
            "change_vol": 100000,
            "ann_date": "20260701",
            "change_ratio": 0.5,
        }
    )
    assert mapped is not None
    assert "减持" in mapped["变动数量"]
    assert mapped["公告日期"] == "2026-07-01"

    fused = fuse_share_reduce(
        [{"变动股东": "李四", "变动数量": "减持50万", "公告日期": "2026-06-20"}],
        [
            {
                "in_de": "DE",
                "holder_name": "张三",
                "change_vol": 100000,
                "ann_date": "20260701",
            },
            {"in_de": "IN", "holder_name": "王五", "ann_date": "20260702"},
        ],
        limit=5,
    )
    assert len(fused) == 2
    assert fused[0]["变动股东"] == "张三"


def test_fuse_macro_series_tushare_primary_and_conflict() -> None:
    ak = [{"月份": "200801", "制造业": 50.0}]
    ts = [{"月份": "202607", "制造业": 49.2, "source": "tushare_cn_pmi"}]
    series, meta = fuse_macro_series(ak, ts, primary="tushare")
    assert series[0]["月份"] == "202607"
    assert meta["primary"] == "tushare"
    assert meta["agreement"] == "conflict"
    assert meta["period_gap_months"] and meta["period_gap_months"] > 1

    match_series, match_meta = fuse_macro_series(
        [{"月份": "202606", "全国同比": 0.1}],
        [{"月份": "202607", "全国同比": 0.2}],
        primary="tushare",
        period_tol_months=1,
    )
    assert match_series[0]["月份"] == "202607"
    assert match_meta["agreement"] == "match"


def test_fuse_margin_trend_and_detail() -> None:
    fused = fuse_margin_trend(
        {"financing_balance_change_5d_pct": 1.2, "latest": {"融资余额": 1}},
        {"financing_balance_change_5d_pct": 1.0, "latest": {"融资余额": 2}, "trade_date": "20260807"},
    )
    assert fused is not None
    assert fused["source"] == "akshare"
    assert fused["agreement"] == "match"
    assert "tushare" in fused

    conflict = fuse_margin_trend(
        {"financing_balance_change_5d_pct": 2.0, "latest": {}},
        {"financing_balance_change_5d_pct": -2.0, "latest": {}},
    )
    assert conflict is not None
    assert conflict["agreement"] == "conflict"

    detail = fuse_margin_detail(
        [{"融资余额": 1, "source": "akshare_sse"}],
        [{"rzye": 2, "trade_date": "20260807"}],
        prefer="tushare",
    )
    assert detail[0]["rzye"] == 2
    assert detail[0]["source"] == "tushare_margin_detail"
