"""Tushare daily_basic 估值 overlay 单测：东财现货持续失败时的 PE/PB 兜底链路。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import pytest

from money_more.data.fetcher import (
    _bump_em_spot_health,
    _overlay_tushare_valuation,
    fetch_spot_with_fallback,
    make_tushare_valuation_overlay,
    spot_valuation_coverage,
)
from money_more.data.tushare_source import TushareSource


@pytest.fixture(autouse=True)
def _isolated_em_health(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(
        "money_more.data.fetcher._em_health_path", lambda: tmp_path / "em_health.json"
    )


class _MemCache:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def set(self, key: str, value: Any, ttl_sec: int | None = None) -> None:
        self._store[key] = value

    def get_stale(self, key: str) -> Any | None:
        return self._store.get(key)


def _sina_df() -> pd.DataFrame:
    # 新浪现货：无 PE/PB/总市值列
    return pd.DataFrame(
        [
            {"代码": "sh600519", "名称": "贵州茅台", "最新价": 1400.0, "涨跌幅": 0.5, "成交额": 3e9},
            {"代码": "sz000001", "名称": "平安银行", "最新价": 10.0, "涨跌幅": -0.2, "成交额": 1e9},
        ]
    )


# —— daily_basic 拉取 ——


def test_daily_basic_valuation_stepback_over_weekend(monkeypatch: pytest.MonkeyPatch) -> None:
    """as_of 当天/前一天空（周末）→ 回退到最近交易日；行映射正确。"""
    src = TushareSource(token="x", as_of=date(2026, 9, 20))  # 周日
    src.available = True
    src._pro = object()
    seen: list[str] = []

    def fake_call(method: str, **kwargs) -> pd.DataFrame:
        assert method == "daily_basic"
        td = kwargs["trade_date"]
        seen.append(td)
        if td != "20260918":  # 周五才非空
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "trade_date": td,
                    "pe": 21.0,
                    "pe_ttm": 22.5,
                    "pb": 8.1,
                    "total_mv": 176000000.0,  # 万元
                    "circ_mv": 175000000.0,
                    "turnover_rate": 0.3,
                }
            ]
        )

    monkeypatch.setattr(src, "_safe_call", fake_call)
    out = src.fetch_daily_basic_valuation()
    assert out["as_of"] == "20260918"
    assert seen == ["20260920", "20260919", "20260918"]
    row = out["rows"]["600519"]
    assert row["pe_ttm"] == 22.5 and row["pb"] == 8.1
    assert row["total_mv"] == 176000000.0


def test_daily_basic_valuation_permission_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """权限不足（2000 积分不够）：不逐日重试，直接空 rows + 记错。"""
    src = TushareSource(token="x", as_of=date(2026, 9, 18))
    src.available = True
    src._pro = object()
    calls = {"n": 0}

    def fake_call(method: str, **kwargs) -> pd.DataFrame:
        calls["n"] += 1
        raise PermissionError("抱歉，您没有访问 daily_basic 的权限")

    monkeypatch.setattr(src, "_safe_call", fake_call)
    out = src.fetch_daily_basic_valuation()
    assert out["rows"] == {} and calls["n"] == 1
    assert any("daily_basic@20260918" in e for e in out["errors"])


def test_daily_basic_valuation_unavailable() -> None:
    src = TushareSource(token="", as_of=date(2026, 9, 18))
    out = src.fetch_daily_basic_valuation()
    assert out["rows"] == {} and "tushare_unavailable" in out["errors"]


# —— overlay 纯函数 ——


def test_overlay_fills_missing_and_keeps_existing() -> None:
    live = pd.DataFrame(
        [
            # 600519：无任何估值列；000001：已有市净率（不被覆盖）
            {"代码": "600519", "名称": "贵州茅台", "最新价": 1400.0},
            {"代码": "000001", "名称": "平安银行", "最新价": 10.0, "市净率": 0.55},
        ]
    )
    rows = {
        "600519": {"pe_ttm": 22.5, "pb": 8.1, "total_mv": 176000000.0},
        "000001": {"pe_ttm": 5.1, "pb": 0.6, "total_mv": 20000000.0},
    }
    out = _overlay_tushare_valuation(live, rows)
    mout = out.set_index("代码")
    assert float(mout.loc["600519", "市盈率TTM"]) == 22.5
    assert float(mout.loc["600519", "市净率"]) == 8.1
    # 万元 → 元
    assert float(mout.loc["600519", "总市值"]) == 176000000.0 * 1e4
    # 已有值不覆盖
    assert float(mout.loc["000001", "市净率"]) == 0.55
    assert float(mout.loc["000001", "市盈率TTM"]) == 5.1


def test_overlay_noop_on_empty() -> None:
    live = _sina_df()
    assert _overlay_tushare_valuation(live, None) is live
    assert _overlay_tushare_valuation(live, {}) is live
    assert _overlay_tushare_valuation(pd.DataFrame(), {"600519": {}}).empty


def test_make_overlay_memoizes_and_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    class _TS:
        def fetch_daily_basic_valuation(self) -> dict[str, Any]:
            calls["n"] += 1
            return {"as_of": "20260918", "rows": {"600519": {"pe_ttm": 22.5, "pb": 8.1, "total_mv": 1.0}}}

    overlay = make_tushare_valuation_overlay(_TS())
    df1 = overlay(_sina_df())
    df2 = overlay(_sina_df())
    assert calls["n"] == 1  # 只拉一次
    cov = spot_valuation_coverage(df1)
    assert cov["pe_ok"] == 1 and cov["n"] == 2
    assert spot_valuation_coverage(df2)["pe_ok"] == 1

    class _BadTS:
        def fetch_daily_basic_valuation(self) -> dict[str, Any]:
            raise RuntimeError("boom")

    safe = make_tushare_valuation_overlay(_BadTS())
    out = safe(_sina_df())
    assert spot_valuation_coverage(out)["pe_ok"] == 0  # 异常 → no-op


# —— 全链路：东财三连败 → 新浪 + tushare overlay ——


def test_spot_fallback_sina_then_tushare_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _MemCache()

    def _em_fail() -> pd.DataFrame:
        raise ConnectionError("push2 RemoteDisconnected")

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot_em", _em_fail)
    monkeypatch.setattr("money_more.data.fetcher._fetch_em_split_spot", lambda: pd.DataFrame())
    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot", _sina_df)
    monkeypatch.setattr("money_more.data.fetcher.time.sleep", lambda *_a, **_k: None)

    overlay = make_tushare_valuation_overlay(
        type(
            "_TS",
            (),
            {
                "fetch_daily_basic_valuation": lambda self: {
                    "as_of": "20260918",
                    "rows": {
                        "600519": {"pe_ttm": 22.5, "pb": 8.1, "total_mv": 176000000.0},
                        "000001": {"pe_ttm": 5.1, "pb": 0.6, "total_mv": 20000000.0},
                    },
                }
            },
        )()
    )
    df, source, warnings = fetch_spot_with_fallback(
        cache_key="spot:test", cache=cache, valuation_overlay=overlay
    )
    assert source == "sina"
    cov = spot_valuation_coverage(df)
    assert cov["pe_ok"] == 2 and cov["pb_ok"] == 2
    assert any("spot_tushare_valuation_overlay:pe=2/2" in w for w in warnings)
    # 最新价仍是新浪的（overlay 不碰价格列）
    assert float(df.set_index("代码").loc["600519", "最新价"]) == 1400.0


def test_spot_overlay_skipped_when_pe_coverage_already_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """东财缓存 overlay 已把 PE 补到 ≥50%：不再调 tushare（省一次全市场拉取）。"""
    cache = _MemCache()
    cache._store["spot:em_valuation"] = [
        {"代码": "600519", "名称": "贵州茅台", "市盈率-动态": 22.5, "市净率": 8.1},
        {"代码": "000001", "名称": "平安银行", "市盈率-动态": 5.1, "市净率": 0.6},
    ]

    def _em_fail() -> pd.DataFrame:
        raise ConnectionError("push2 down")

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot_em", _em_fail)
    monkeypatch.setattr("money_more.data.fetcher._fetch_em_split_spot", lambda: pd.DataFrame())
    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot", _sina_df)
    monkeypatch.setattr("money_more.data.fetcher.time.sleep", lambda *_a, **_k: None)

    called = {"n": 0}

    def _overlay(df: pd.DataFrame) -> pd.DataFrame:
        called["n"] += 1
        return df

    df, source, warnings = fetch_spot_with_fallback(
        cache_key="spot:test", cache=cache, valuation_overlay=_overlay
    )
    assert source == "sina"
    assert called["n"] == 0
    assert not any("tushare_valuation_overlay" in w for w in warnings)


# —— 健康计数 ——


def test_em_health_counter_accumulates_and_resets(tmp_path) -> None:
    assert _bump_em_spot_health(False) == 1
    assert _bump_em_spot_health(False) == 2
    assert _bump_em_spot_health(True) == 0
    assert _bump_em_spot_health(False) == 1


def test_spot_em_consecutive_failures_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """连续第 2 轮起，errors 带 spot_em_consecutive_failures:N（进错误抽样可见）。"""
    cache = _MemCache()

    def _em_fail() -> pd.DataFrame:
        raise ConnectionError("push2 down")

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot_em", _em_fail)
    monkeypatch.setattr("money_more.data.fetcher._fetch_em_split_spot", lambda: pd.DataFrame())
    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot", _sina_df)
    monkeypatch.setattr("money_more.data.fetcher.time.sleep", lambda *_a, **_k: None)

    _, _, w1 = fetch_spot_with_fallback(cache_key="spot:test", cache=_MemCache())
    assert not any("consecutive_failures" in w for w in w1)  # 第 1 轮不标
    _, _, w2 = fetch_spot_with_fallback(cache_key="spot:test2", cache=cache)
    assert "spot_em_consecutive_failures:2" in w2
    _, _, w3 = fetch_spot_with_fallback(cache_key="spot:test3", cache=_MemCache())
    assert "spot_em_consecutive_failures:3" in w3
