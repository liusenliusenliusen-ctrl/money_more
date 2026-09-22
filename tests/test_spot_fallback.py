"""全 A 现货多源回退单测（无网络）。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import pytest

from money_more.data.cache import DiskTTLCache
from money_more.data.fetcher import (
    MarketDataFetcher,
    _canonicalize_spot_df,
    fetch_spot_with_fallback,
)


class _MemCache:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._stale: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def set(self, key: str, value: Any, ttl_sec: int | None = None) -> None:
        self._store[key] = value

    def get_stale(self, key: str) -> Any | None:
        return self._stale.get(key) or self._store.get(key)


@pytest.fixture(autouse=True)
def _isolated_em_health(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """健康计数落盘隔离到 tmp，避免测试污染真实 data/cache 且互相累计。"""
    monkeypatch.setattr(
        "money_more.data.fetcher._em_health_path", lambda: tmp_path / "em_health.json"
    )


def _em_like(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_daily_hist_prefers_sina_for_screening(monkeypatch: pytest.MonkeyPatch) -> None:
    """筛股均额走新浪，不打东财 K 线（几百只连打会把 push 限流续上）。"""
    from datetime import date

    from money_more.data.fetcher import MarketDataFetcher

    called = {"em": 0, "sina": 0}

    def _em(*_a, **_k):
        called["em"] += 1
        raise ConnectionError("push2his down")

    def _sina(*_a, **_k):
        called["sina"] += 1
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-18", "2026-09-19"]),
                "close": [10.0, 10.2],
                "amount": [1e8, 1.1e8],
            }
        )

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_hist", _em)
    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_daily", _sina)
    fetcher = MarketDataFetcher(as_of=date(2026, 9, 19))
    df = fetcher._fetch_daily_hist("600519", "20260901", "20260919", prefer="sina")
    assert called["em"] == 0 and called["sina"] == 1
    assert len(df) == 2


def test_canonicalize_spot_normalizes_prefixed_codes() -> None:
    raw = pd.DataFrame(
        [
            {"代码": "sh600519", "名称": "贵州茅台", "最新价": 1400, "涨跌幅": 1.0, "成交额": 1e9},
            {"代码": "600519", "名称": "贵州茅台", "最新价": 1400, "涨跌幅": 1.0, "成交额": 1e9},
        ]
    )
    out = _canonicalize_spot_df(raw)
    assert list(out["代码"]) == ["600519"]


def test_fetch_spot_falls_back_to_sina(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _MemCache()

    def _em_fail() -> pd.DataFrame:
        raise ConnectionError("push2 proxy")

    def _split_empty() -> pd.DataFrame:
        return pd.DataFrame()

    def _sina_ok() -> pd.DataFrame:
        return _em_like(
            [
                {"代码": "sh601398", "名称": "工商银行", "最新价": 5.0, "涨跌幅": 0.5, "成交额": 2e9},
                {"代码": "sz000001", "名称": "平安银行", "最新价": 10.0, "涨跌幅": -0.2, "成交额": 1e9},
            ]
        )

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot_em", _em_fail)
    monkeypatch.setattr("money_more.data.fetcher._fetch_em_split_spot", _split_empty)
    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot", _sina_ok)
    monkeypatch.setattr("money_more.data.fetcher.time.sleep", lambda *_a, **_k: None)

    df, source, warnings = fetch_spot_with_fallback(cache_key="spot:test", cache=cache)
    assert source == "sina"
    assert len(df) == 2
    assert set(df["代码"]) == {"601398", "000001"}
    assert any("spot_fallback:sina" in w for w in warnings)
    assert "spot_em_attempts:1" in warnings


def test_fetch_spot_em_fails_once_then_sina(monkeypatch: pytest.MonkeyPatch) -> None:
    """东财全量只打一次。失败后不再整段重试，也不再打分市场（同一 clist 接口）。"""
    cache = _MemCache()
    calls = {"n": 0, "split": 0}

    def _em_fail() -> pd.DataFrame:
        calls["n"] += 1
        raise ConnectionError("push2 RemoteDisconnected")

    def _split() -> pd.DataFrame:
        calls["split"] += 1
        return pd.DataFrame()

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot_em", _em_fail)
    monkeypatch.setattr("money_more.data.fetcher._fetch_em_split_spot", _split)
    monkeypatch.setattr(
        "money_more.data.fetcher.ak.stock_zh_a_spot",
        lambda: _em_like(
            [{"代码": "sh600519", "名称": "贵州茅台", "最新价": 1400, "涨跌幅": 1.0, "成交额": 1e9}]
        ),
    )

    df, source, warnings = fetch_spot_with_fallback(cache_key="spot:test3", cache=cache)
    assert source == "sina"
    assert calls["n"] == 1
    assert calls["split"] == 0
    assert len(df) == 1
    assert "spot_em_attempts:1" in warnings


def test_fetch_spot_uses_stale_cache_when_live_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _MemCache()
    cache._stale["spot:test"] = [
        {"代码": "600036", "名称": "招商银行", "最新价": 35, "涨跌幅": 0.1, "成交额": 1e9},
    ]

    monkeypatch.setattr(
        "money_more.data.fetcher.ak.stock_zh_a_spot_em",
        lambda: (_ for _ in ()).throw(RuntimeError("em down")),
    )
    monkeypatch.setattr("money_more.data.fetcher._fetch_em_split_spot", lambda: pd.DataFrame())
    monkeypatch.setattr(
        "money_more.data.fetcher.ak.stock_zh_a_spot",
        lambda: (_ for _ in ()).throw(RuntimeError("sina down")),
    )
    monkeypatch.setattr("money_more.data.fetcher.time.sleep", lambda *_a, **_k: None)

    df, source, warnings = fetch_spot_with_fallback(cache_key="spot:test", cache=cache)
    assert source == "stale_cache"
    assert len(df) == 1
    assert df.iloc[0]["代码"] == "600036"
    assert "spot_stale_cache" in warnings


def test_market_fetcher_get_spot_records_source(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = MarketDataFetcher(as_of=date(2026, 7, 19))

    def _fake_fetch(*, cache_key: str, cache: Any, **_kw: Any) -> tuple[pd.DataFrame, str, list[str]]:
        df = _em_like(
            [{"代码": "600519", "名称": "贵州茅台", "最新价": 1400, "涨跌幅": 0.5, "成交额": 3e9}]
        )
        return df, "sina", ["spot_fallback:sina"]

    monkeypatch.setattr("money_more.data.fetcher.fetch_spot_with_fallback", _fake_fetch)
    spot = fetcher._get_spot_df()
    assert not spot.empty
    assert fetcher.spot_source == "sina"
    assert spot.iloc[0]["代码"] == "600519"


def test_overlay_em_valuation_keeps_sina_price() -> None:
    from money_more.data.fetcher import _overlay_em_valuation, spot_valuation_coverage

    live = _em_like([{"代码": "600519", "名称": "贵州茅台", "最新价": 1401, "涨跌幅": 0.2}])
    em_rows = [
        {"代码": "600519", "名称": "贵州茅台", "最新价": 1390, "市盈率-动态": 22.5, "市净率": 8.1}
    ]
    out = _overlay_em_valuation(live, em_rows)
    assert float(out.iloc[0]["最新价"]) == 1401
    assert float(out.iloc[0]["市盈率-动态"]) == 22.5
    assert float(out.iloc[0]["市净率"]) == 8.1
    cov = spot_valuation_coverage(out)
    assert cov["pe_ok"] == 1
    assert cov["pb_ok"] == 1
    assert cov["n"] == 1


def test_fetch_spot_overlay_records_pe_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _MemCache()
    cache._stale["spot:em_valuation"] = [
        {"代码": "601398", "名称": "工商银行", "最新价": 4.8, "市盈率-动态": 6.2, "市净率": 0.6}
    ]

    def _em_fail() -> pd.DataFrame:
        raise ConnectionError("push2 proxy")

    def _sina_ok() -> pd.DataFrame:
        return _em_like(
            [{"代码": "sh601398", "名称": "工商银行", "最新价": 5.0, "涨跌幅": 0.5, "成交额": 2e9}]
        )

    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot_em", _em_fail)
    monkeypatch.setattr("money_more.data.fetcher._fetch_em_split_spot", lambda: pd.DataFrame())
    monkeypatch.setattr("money_more.data.fetcher.ak.stock_zh_a_spot", _sina_ok)
    monkeypatch.setattr("money_more.data.fetcher.time.sleep", lambda *_a, **_k: None)

    df, source, warnings = fetch_spot_with_fallback(cache_key="spot:test", cache=cache)
    assert source == "sina"
    assert float(df.iloc[0]["市盈率-动态"]) == 6.2
    overlay = [w for w in warnings if "em_valuation_overlay" in w]
    assert overlay
    assert "pe=1/1" in overlay[0]
