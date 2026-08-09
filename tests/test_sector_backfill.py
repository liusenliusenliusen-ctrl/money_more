"""深度池补跑 B1：缺板块判定。"""

from __future__ import annotations

from money_more.analysis.sector_backfill import (
    infer_deep_pool_sectors,
    sector_already_analyzed,
    sectors_needing_backfill,
)


def test_infer_deep_pool_sectors_from_candidates_and_hardcode() -> None:
    # 茅台硬编码白酒；宁德硬编码新能源；候选可覆盖未知票
    secs = infer_deep_pool_sectors(
        ["600519", "300750", "601166"],
        top_candidates=[
            {"code": "601166", "sector": "银行", "in_deep": True},
            {"code": "600519", "sector": "白酒", "in_deep": True},
        ],
    )
    assert secs == ["白酒", "新能源", "银行"]


def test_sector_already_analyzed_fuzzy() -> None:
    existing = ["半导体板块", "银行"]
    assert sector_already_analyzed("半导体", existing)
    assert sector_already_analyzed("股份制银行", existing)
    assert not sector_already_analyzed("医药", existing)


def test_sectors_needing_backfill_skips_covered_and_caps() -> None:
    analyses = [
        {"sector": "白酒", "analysis": {"sector": "白酒", "priority": "high"}},
        {"sector": "半导体", "analysis": {"sector": "半导体"}},
    ]
    missing = sectors_needing_backfill(
        ["白酒", "银行", "医药", "新能源", "家电"],
        analyses,
        max_backfill=2,
    )
    assert missing == ["银行", "医药"]
    assert "白酒" not in missing
    assert "半导体" not in missing


def test_no_backfill_when_all_covered() -> None:
    analyses = [{"sector": "银行", "analysis": {"sector": "银行"}}]
    assert sectors_needing_backfill(["银行", "股份制银行"], analyses) == []
