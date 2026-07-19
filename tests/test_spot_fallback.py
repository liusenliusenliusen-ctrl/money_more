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


def _em_like(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


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

    df, source, warnings = fetch_spot_with_fallback(cache_key="spot:test", cache=cache)
    assert source == "sina"
    assert len(df) == 2
    assert set(df["代码"]) == {"601398", "000001"}
    assert any("spot_fallback:sina" in w for w in warnings)


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

    df, source, warnings = fetch_spot_with_fallback(cache_key="spot:test", cache=cache)
    assert source == "stale_cache"
    assert len(df) == 1
    assert df.iloc[0]["代码"] == "600036"
    assert "spot_stale_cache" in warnings


def test_market_fetcher_get_spot_records_source(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = MarketDataFetcher(as_of=date(2026, 7, 19))

    def _fake_fetch(*, cache_key: str, cache: Any) -> tuple[pd.DataFrame, str, list[str]]:
        df = _em_like(
            [{"代码": "600519", "名称": "贵州茅台", "最新价": 1400, "涨跌幅": 0.5, "成交额": 3e9}]
        )
        return df, "sina", ["spot_fallback:sina"]

    monkeypatch.setattr("money_more.data.fetcher.fetch_spot_with_fallback", _fake_fetch)
    spot = fetcher._get_spot_df()
    assert not spot.empty
    assert fetcher.spot_source == "sina"
    assert spot.iloc[0]["代码"] == "600519"
