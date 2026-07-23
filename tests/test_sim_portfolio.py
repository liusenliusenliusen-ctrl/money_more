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
    assert "本轮模拟操作说明" in text


def test_sim_explains_no_trade_when_all_watch() -> None:
    from money_more.sim.engine import build_sim_round_explanation, render_sim_section

    result = {
        "recommendations": [
            {
                "code": "300750",
                "action": "watch",
                "position_pct": 0,
                "rationale": "微观结构liquidity_stress禁止新买",
            },
            {
                "code": "601899",
                "action": "watch",
                "position_pct": 0,
                "rationale": "风控后观察",
            },
        ],
        "decision_summary": {
            "portfolio_summary": "终局无可执行新开仓。主因：微观结构 liquidity_stress。",
        },
        "validation_overrides": [
            "microstructure=liquidity_stress → 抑制新开仓",
            "300750: 微观结构liquidity_stress禁止新买 → watch",
        ],
    }
    sim = {
        "initial_cash": 50000,
        "cash": 50000,
        "equity": 50000,
        "market_value": 0,
        "nav_return_pct": 0.0,
        "positions": [],
        "fills": [
            {
                "stock_code": "300750",
                "action_src": "watch",
                "note": "终局为观察且模拟盘无该仓：不开仓",
                "why": "微观结构liquidity_stress禁止新买",
                "skipped": True,
            }
        ],
        "fill_count": 0,
    }
    expl = build_sim_round_explanation(sim, result)
    assert "无成交" in expl["headline"]
    assert any("无可执行开仓" in b for b in expl["bullets"])
    assert any("liquidity_stress" in b for b in expl["bullets"])

    text = "\n".join(render_sim_section(sim, result=result))
    assert "本轮模拟操作说明" in text
    assert "无可执行开仓" in text
    assert "未成交 / 不调仓明细" in text
    assert "300750" in text


def test_sim_fill_carries_why(tmp_path: Path) -> None:
    db = Database(tmp_path / "sim_why.db")
    engine = SimPortfolioEngine(db, SimConfig(initial_cash=50_000))
    snap = engine.apply_recommendations(
        run_id=1,
        run_date="2026-07-01",
        recommendations=[
            {
                "code": "601318",
                "action": "buy",
                "position_pct": 10,
                "rationale": "低估值保险龙头，分批建仓",
            }
        ],
        quotes={"601318": 50.0},
    )
    real = [f for f in snap["fills"] if not f.get("skipped")]
    assert len(real) == 1
    assert "why" in real[0]
    assert "10%" in real[0]["why"] or "buy" in real[0]["why"]
    assert "低估值" in real[0]["why"]
