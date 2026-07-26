"""个股遴选漏斗单测（无网络）。"""

from __future__ import annotations

import pandas as pd

from money_more.analysis.screen import (
    _apply_hard_filters,
    _normalize_spot,
    _score_universe,
    run_stock_screen,
)
from money_more.config import ScreenConfig


class _FakeFetcher:
    def __init__(self, spot: pd.DataFrame) -> None:
        self._spot = spot

    def _get_spot_df(self) -> pd.DataFrame:
        return self._spot.copy()

    def list_sector_constituent_codes(self, sector_name: str, limit: int = 60) -> list[str]:
        if sector_name == "银行":
            return ["601318", "600036", "601398"][:limit]
        if sector_name == "白酒":
            return ["600519", "000858"][:limit]
        return []


def _sample_spot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"代码": "601318", "名称": "中国平安", "最新价": 50, "涨跌幅": 1.0, "成交额": 2e9, "市盈率-动态": 8, "市净率": 0.9},
            {"代码": "600036", "名称": "招商银行", "最新价": 35, "涨跌幅": 0.5, "成交额": 1e9, "市盈率-动态": 6, "市净率": 0.8},
            {"代码": "600519", "名称": "贵州茅台", "最新价": 1400, "涨跌幅": -0.5, "成交额": 3e9, "市盈率-动态": 20, "市净率": 7},
            {"代码": "300750", "名称": "宁德时代", "最新价": 200, "涨跌幅": 2.0, "成交额": 2e9, "市盈率-动态": 25, "市净率": 4},
            {"代码": "000001", "名称": "平安银行", "最新价": 10, "涨跌幅": 0.2, "成交额": 8e8, "市盈率-动态": 5, "市净率": 0.6},
            {"代码": "000002", "名称": "*ST示例", "最新价": 2, "涨跌幅": -5, "成交额": 1e8, "市盈率-动态": 3, "市净率": 0.5},
            {"代码": "688001", "名称": "微盘股", "最新价": 20, "涨跌幅": 10, "成交额": 1e6, "市盈率-动态": 100, "市净率": 20},
        ]
    )


def test_filters_exclude_st_and_illiquid() -> None:
    cfg = ScreenConfig(min_amount=5e7, pe_max=80, exclude_st=True)
    df = _normalize_spot(_sample_spot())
    out, stats = _apply_hard_filters(df, cfg)
    codes = set(out["code"].tolist())
    assert "000002" not in codes  # ST
    assert "688001" not in codes  # low amount / high PE
    assert stats["st"] >= 1


def test_run_screen_expands_beyond_force_holdings() -> None:
    cfg = ScreenConfig(
        enabled=True,
        universe_mode="sector_spot",
        max_universe=100,
        max_quant=10,
        max_deep=5,
        min_amount=1e7,
        pe_max=90,
    )
    fetcher = _FakeFetcher(_sample_spot())
    result = run_stock_screen(
        fetcher,  # type: ignore[arg-type]
        config=cfg,
        watch_sectors=["银行", "白酒"],
        force_codes=["600519", "300750"],  # 模拟声明持仓强制进池
        sector_analyses=[{"sector": "银行", "analysis": {"priority": "high", "sector": "银行"}}],
    )
    deep = result["deep_codes"]
    assert "600519" in deep and "300750" in deep
    # 持仓强制不占 max_deep：深度池 ≤ 强制数 + max_deep
    assert len(deep) <= 2 + 5
    assert int(result.get("screened_added") or 0) <= 5
    assert any(c in deep for c in ("601318", "600036", "000001", "601398"))
    assert result["universe_size"] >= 3
    assert result.get("force_codes") == ["600519", "300750"]


def test_screen_empty_force_is_pure_quant() -> None:
    cfg = ScreenConfig(
        enabled=True,
        universe_mode="spot_all",
        max_universe=100,
        max_quant=10,
        max_deep=3,
        min_amount=1e7,
        pe_max=90,
    )
    result = run_stock_screen(
        _FakeFetcher(_sample_spot()),  # type: ignore[arg-type]
        config=cfg,
        watch_sectors=[],
        force_codes=[],
    )
    assert result.get("force_codes") == []
    assert len(result["deep_codes"]) <= 3
    assert int(result.get("screened_added") or 0) == len(result["deep_codes"])


def test_screen_disabled_only_force() -> None:
    cfg = ScreenConfig(enabled=False)
    result = run_stock_screen(
        _FakeFetcher(_sample_spot()),  # type: ignore[arg-type]
        config=cfg,
        watch_sectors=["银行"],
        force_codes=["600519"],
    )
    assert result["deep_codes"] == ["600519"]
    assert result["coverage_mode"] == "force_only"


def test_screen_spot_empty_degraded() -> None:
    class _Empty:
        def _get_spot_df(self):
            return None

        def list_sector_constituent_codes(self, *a, **k):
            return []

    result = run_stock_screen(
        _Empty(),  # type: ignore[arg-type]
        config=ScreenConfig(enabled=True),
        watch_sectors=["银行"],
        force_codes=["600519", "300750"],
    )
    assert result["ok"] is False
    assert result["degraded"] is True
    assert "spot_empty" in (result.get("errors") or [])
    assert result["deep_codes"] == ["600519", "300750"]


def test_score_prefers_cheap_pe() -> None:
    df = _normalize_spot(_sample_spot())
    df, _ = _apply_hard_filters(df, ScreenConfig(min_amount=1e7, pe_max=90))
    scored = _score_universe(df, [], 0)
    cheap = scored[scored["code"] == "000001"]["screen_score"].iloc[0]
    rich = scored[scored["code"] == "300750"]["screen_score"].iloc[0]
    assert cheap > rich
