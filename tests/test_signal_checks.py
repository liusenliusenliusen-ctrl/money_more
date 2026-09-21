"""验证信号机械校验（signal_checks）单测：解析、判定、台账接线。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from money_more.analysis.signal_checks import (
    build_signal_context,
    check_signal,
    parse_verify_signal,
    summarize_signal_checks,
)


def test_parse_us10y() -> None:
    assert parse_verify_signal("美债10Y回落至4.2%以下") == {
        "indicator": "us10y",
        "op": "<=",
        "threshold": 4.2,
        "unit": "%",
    }
    spec = parse_verify_signal("美债10Y站稳4.8%")
    assert spec and spec["op"] == ">=" and spec["threshold"] == 4.8


def test_parse_market_amount() -> None:
    spec = parse_verify_signal("日均成交额站稳9500亿")
    assert spec and spec["indicator"] == "market_amount"
    assert spec["op"] == ">=" and spec["threshold"] == 9500e8
    spec2 = parse_verify_signal("两市成交额突破1.5万亿")
    assert spec2 and spec2["threshold"] == 1.5e12


def test_parse_margin_pmi_sector() -> None:
    spec = parse_verify_signal("两融余额止跌回升")
    assert spec and spec["indicator"] == "margin_5d" and spec["op"] == ">="
    spec2 = parse_verify_signal("PMI重回荣枯线上方")
    assert spec2 and spec2["indicator"] == "pmi" and spec2["threshold"] == 50.0
    spec3 = parse_verify_signal("制造业PMI环比回升")
    assert spec3 and spec3["indicator"] == "pmi_mom"
    spec4 = parse_verify_signal("板块主力资金净流入转正")
    assert spec4 and spec4["indicator"] == "sector_flow"


def test_parse_narrative_signals() -> None:
    # 无稳定数据源的必须走叙事，不硬猜
    assert parse_verify_signal("中秋动销显著超预期") is None
    assert parse_verify_signal("iPhone出货量连续两季增长") is None
    assert parse_verify_signal("批价回升至2400元") is None
    assert parse_verify_signal("") is None


def _ctx() -> dict:
    return build_signal_context(
        macro_raw={
            "global_liquidity": {"us_10y": {"latest": 4.5}},
            "margin_trend": {"financing_balance_change_5d_pct": 0.8},
            "macro_hard": {"pmi": [{"value": 49.8}, {"value": 49.2}]},
            "sector_money_flow": {
                "top_inflow": [{"板块": "通信设备", "净流入": 2.2e10}],
            },
        },
        turnover={"latest": 1.05e12, "avg5": 1.0e12, "avg20": 9.6e11, "as_of": "2026-09-18"},
    )


def test_check_signal_verdicts() -> None:
    ctx = _ctx()
    # 美债 4.5 > 4.2：条件「回落至4.2以下」未达成
    assert check_signal("美债10Y回落至4.2%以下", ctx)["verdict"] == "unmet"
    # 日均成交额 9600 亿 ≥ 9500 亿：达成
    c = check_signal("日均成交额站稳9500亿", ctx)
    assert c["verdict"] == "met" and c["measured"] == 9.6e11
    # 两融 5 日 +0.8%：「止跌回升」达成
    assert check_signal("两融余额止跌回升", ctx)["verdict"] == "met"
    # PMI 49.8 < 50：荣枯线未达成；环比 +0.6：回升达成
    assert check_signal("PMI重回荣枯线上方", ctx)["verdict"] == "unmet"
    assert check_signal("制造业PMI环比回升", ctx)["verdict"] == "met"
    # 板块资金：通信 ↔ 通信设备 子串匹配
    c2 = check_signal("主力资金净流入", ctx, sector="通信")
    assert c2["verdict"] == "met" and "通信设备" in c2["detail"]
    # 板块不在资金流表内 → unknown
    assert check_signal("主力资金净流入", ctx, sector="白酒")["verdict"] == "unknown"
    # 叙事
    assert check_signal("中秋动销超预期", ctx)["verdict"] == "narrative"


def test_check_signal_missing_indicator_is_unknown() -> None:
    ctx = build_signal_context(macro_raw={}, turnover={})
    out = check_signal("美债10Y回落至4.2%以下", ctx)
    assert out["checkable"] is True and out["verdict"] == "unknown"


def test_verify_ledger_attaches_signal_checks(tmp_path: Path) -> None:
    from money_more.analysis.verify_tracker import build_verify_ledger

    dig = tmp_path / "digests"
    dig.mkdir()
    (dig / "2026-09-01.json").write_text(
        json.dumps(
            {
                "run_date": "2026-09-01",
                "recommendations": [
                    {
                        "code": "300059",
                        "action": "watch",
                        "position_pct": 0,
                        "verify_in_days": 14,
                        "verify_signals": ["日均成交额站稳9500亿", "中秋动销超预期"],
                        "sector_tag": "证券",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class _F:
        def _fetch_daily_hist(self, code, start, end):  # noqa: ANN001
            import pandas as pd

            return pd.DataFrame({"收盘": [18.0, 18.5]})

    ledger = build_verify_ledger(
        digests_dir=dig,
        fetcher=_F(),
        as_of=date(2026, 9, 18),
        signal_context=_ctx(),
    )
    row = next(r for r in ledger["rows"] if r["code"] == "300059")
    checks = row["signal_checks"]
    assert checks[0]["verdict"] == "met"  # 成交额条件机查达成
    assert checks[1]["verdict"] == "narrative"  # 动销仍需叙事判断
    cov = ledger["signal_coverage"]
    assert cov["total"] == 2 and cov["checkable"] == 1 and cov["narrative"] == 1
    assert summarize_signal_checks(ledger["rows"])["met"] >= 1


def test_fetch_market_turnover_aggregation(monkeypatch) -> None:
    """index_daily 沪+深按日求和；千元→元；同日不齐的日子不用。"""
    import pandas as pd

    from money_more.data.tushare_source import TushareSource

    src = TushareSource(token="x", as_of=date(2026, 9, 18))
    src.available = True
    src._pro = object()

    def fake_call(method: str, **kwargs) -> pd.DataFrame:
        assert method == "index_daily"
        code = kwargs["ts_code"]
        base = 8.0e8 if code == "000001.SH" else 1.0e9  # 千元
        rows = [
            {"trade_date": f"202609{d:02d}", "amount": base + d * 1e6}
            for d in range(1, 22)
        ]
        return pd.DataFrame(rows)

    monkeypatch.setattr(src, "_safe_call", fake_call)
    out = src.fetch_market_turnover(days=25)
    assert out["errors"] == []
    assert out["as_of"] == "20260921"
    # 每日合计 = (8e8 + d*1e6 + 1e9 + d*1e6) 千元 → 元
    d = 21
    expect = (8.0e8 + d * 1e6 + 1.0e9 + d * 1e6) * 1000.0
    assert out["latest"] == round(expect, 0)
    assert out["avg20"] is not None and out["avg20"] > 0
