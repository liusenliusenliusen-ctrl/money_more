"""模拟组合：按报告动作成交与净值。"""

from __future__ import annotations

from pathlib import Path

from money_more.sim import SimConfig, SimPortfolioEngine, render_sim_section
from money_more.storage.db import Database


def test_sim_buy_hold_sell_nav(tmp_path: Path) -> None:
    db = Database(tmp_path / "sim.db")
    engine = SimPortfolioEngine(db, SimConfig(initial_cash=50_000, default_buy_pct=10))

    # Day1: buy 601318 目标 10%
    snap1 = engine.apply_recommendations(
        run_id=1,
        run_date="2026-07-01",
        recommendations=[{"code": "601318", "action": "buy", "position_pct": 10}],
        quotes={"601318": 50.0},
        max_single_pct=20,
        max_total_pct=80,
    )
    assert snap1["fill_count"] == 1
    assert snap1["equity"] < 50_000  # 有手续费
    assert any(p["code"] == "601318" for p in snap1["positions"])
    shares = next(p["shares"] for p in snap1["positions"] if p["code"] == "601318")
    assert shares >= 100
    assert shares % 100 == 0

    # Day2: 股价涨，hold 无成交，净值上升
    snap2 = engine.apply_recommendations(
        run_id=2,
        run_date="2026-07-08",
        recommendations=[{"code": "601318", "action": "hold", "position_pct": 10}],
        quotes={"601318": 55.0},
    )
    assert snap2["fill_count"] == 0
    assert snap2["equity"] > snap1["equity"]
    assert snap2["nav_return_pct"] > snap1["nav_return_pct"]

    # Day3: sell 清仓
    snap3 = engine.apply_recommendations(
        run_id=3,
        run_date="2026-07-15",
        recommendations=[{"code": "601318", "action": "sell"}],
        quotes={"601318": 55.0},
    )
    assert snap3["fill_count"] == 1
    assert snap3["positions"] == []
    assert snap3["cash"] == snap3["equity"]
    assert snap3["nav_return_pct"] > 0


def test_sim_rerun_same_day_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "sim2.db")
    engine = SimPortfolioEngine(db, SimConfig(initial_cash=50_000))
    recs = [{"code": "600519", "action": "buy", "position_pct": 10}]
    quotes = {"600519": 1400.0}
    a = engine.apply_recommendations(run_id=1, run_date="2026-07-10", recommendations=recs, quotes=quotes)
    b = engine.apply_recommendations(run_id=1, run_date="2026-07-10", recommendations=recs, quotes=quotes)
    assert a["equity"] == b["equity"]
    assert a["cash"] == b["cash"]
    assert len(db.sim_list_snapshots()) == 1


def test_sim_reset(tmp_path: Path) -> None:
    db = Database(tmp_path / "sim3.db")
    engine = SimPortfolioEngine(db, SimConfig(initial_cash=50_000))
    engine.apply_recommendations(
        run_id=1,
        run_date="2026-07-01",
        recommendations=[{"code": "300750", "action": "buy", "position_pct": 15}],
        quotes={"300750": 200.0},
    )
    engine.reset()
    st = engine.status()
    assert st["cash"] == 50_000
    assert st["positions"] == []
    assert st["equity"] == 50_000


def test_sim_skips_buy_without_position_pct(tmp_path: Path) -> None:
    db = Database(tmp_path / "sim_skip.db")
    engine = SimPortfolioEngine(db, SimConfig(initial_cash=50_000, default_buy_pct=10))
    snap = engine.apply_recommendations(
        run_id=1,
        run_date="2026-07-01",
        recommendations=[{"code": "601318", "action": "buy"}],  # 无 position_pct
        quotes={"601318": 50.0},
    )
    assert snap["fill_count"] == 0
    assert snap["positions"] == []
    assert any("position_pct" in str(f.get("note") or "") for f in snap["fills"])


def test_render_sim_section() -> None:
    lines = render_sim_section(
        {
            "initial_cash": 50000,
            "cash": 40000,
            "equity": 51000,
            "market_value": 11000,
            "nav_return_pct": 2.0,
            "positions": [
                {
                    "code": "601318",
                    "shares": 200,
                    "avg_cost": 50,
                    "mark": 55,
                    "value": 11000,
                    "pnl_pct": 10,
                    "weight_pct": 21.5,
                }
            ],
            "fills": [],
        }
    )
    text = "\n".join(lines)
    assert "模拟账本" in text or "附录" in text
    assert "不是你的账户" in text
    assert "<details>" in text
    assert "601318" in text
