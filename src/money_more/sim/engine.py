"""模拟组合：按报告动作成交，跟踪净值与盈亏（与真实 holdings 分离）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from money_more.storage.db import Database
from money_more.utils.logging_util import setup_logging

log = setup_logging()

# 佣金万三（单边）、印花税卖出万五
_COMMISSION_RATE = 0.0003
_STAMP_RATE = 0.0005


@dataclass
class SimConfig:
    enabled: bool = True
    initial_cash: float = 50_000.0
    lot_size: int = 100
    default_buy_pct: float = 10.0  # 报告未给 position_pct 时


def _fee_buy(amount: float) -> float:
    return round(amount * _COMMISSION_RATE, 2)


def _fee_sell(amount: float) -> float:
    return round(amount * (_COMMISSION_RATE + _STAMP_RATE), 2)


def _floor_lot(shares: float, lot: int) -> int:
    if shares <= 0 or lot <= 0:
        return 0
    return int(shares // lot) * lot


class SimPortfolioEngine:
    """按报告 recommendations 调仓，落库并返回本轮快照摘要。"""

    def __init__(self, db: Database, config: SimConfig) -> None:
        self.db = db
        self.config = config

    def ensure_account(self) -> dict[str, Any]:
        return self.db.sim_ensure_account(self.config.initial_cash)

    def reset(self) -> dict[str, Any]:
        return self.db.sim_reset(self.config.initial_cash)

    def status(self, quotes: dict[str, float | None] | None = None) -> dict[str, Any]:
        account = self.ensure_account()
        positions = self.db.sim_get_positions()
        return self._mark_snapshot(
            run_id=None,
            run_date=None,
            account=account,
            positions=positions,
            fills=[],
            quotes=quotes or {},
            persist=False,
        )

    def apply_recommendations(
        self,
        *,
        run_id: int,
        run_date: str,
        recommendations: list[dict[str, Any]],
        quotes: dict[str, float | None],
        max_single_pct: float = 20.0,
        max_total_pct: float = 80.0,
    ) -> dict[str, Any]:
        """根据本轮动作成交；同日重跑会回滚到上一快照再重放。"""
        if not self.config.enabled:
            return {"skipped": True, "reason": "sim.enabled=false"}

        self.ensure_account()
        self._rewind_if_rerun(run_date)

        account = self.db.sim_get_account() or self.ensure_account()
        positions = {p["stock_code"]: dict(p) for p in self.db.sim_get_positions()}
        fills: list[dict[str, Any]] = []

        equity = self._equity(account["cash"], positions, quotes)

        sells = []
        buys = []
        for rec in recommendations:
            action = str(rec.get("action") or "watch").lower().strip()
            code = _norm_code(rec.get("code") or rec.get("stock_code") or "")
            if not code:
                continue
            item = {"code": code, "action": action, "rec": rec}
            if action in ("sell", "reduce"):
                sells.append(item)
            elif action in ("buy", "add"):
                buys.append(item)

        for item in sells:
            code = item["code"]
            px = quotes.get(code)
            if px is None or float(px) <= 0:
                fills.append(_skip_fill(run_id, run_date, code, item["action"], "无行情，跳过"))
                continue
            px = float(px)
            pos = positions.get(code)
            if not pos or float(pos["shares"]) <= 0:
                continue
            if item["action"] == "sell":
                sell_shares = float(pos["shares"])
            else:
                target_pct = _target_pct(item["rec"], default=None)
                if target_pct is not None:
                    target_value = equity * (target_pct / 100.0)
                    target_shares = _floor_lot(target_value / px, self.config.lot_size)
                    sell_shares = max(0.0, float(pos["shares"]) - target_shares)
                    sell_shares = float(_floor_lot(sell_shares, self.config.lot_size))
                else:
                    sell_shares = float(_floor_lot(float(pos["shares"]) / 2.0, self.config.lot_size))
            if sell_shares <= 0:
                continue
            fill = self._execute_sell(
                run_id, run_date, code, sell_shares, px, item["action"], account, positions
            )
            if fill:
                fills.append(fill)
                equity = self._equity(account["cash"], positions, quotes)

        for item in buys:
            code = item["code"]
            px = quotes.get(code)
            if px is None or float(px) <= 0:
                fills.append(_skip_fill(run_id, run_date, code, item["action"], "无行情，跳过"))
                continue
            px = float(px)
            target_pct = _target_pct(item["rec"], default=None)
            if target_pct is None or float(target_pct) <= 0:
                fills.append(
                    _skip_fill(
                        run_id,
                        run_date,
                        code,
                        item["action"],
                        "报告未给出 position_pct，模拟盘不默认开仓（避免静默按 10%）",
                    )
                )
                continue
            target_pct = min(float(target_pct), float(max_single_pct))
            target_value = equity * (target_pct / 100.0)
            cur_shares = float((positions.get(code) or {}).get("shares") or 0)
            cur_value = cur_shares * px
            need_value = target_value - cur_value
            if need_value <= px * self.config.lot_size * 0.5:
                continue
            invested = sum(
                float(p["shares"]) * float(quotes.get(c) or 0)
                for c, p in positions.items()
                if quotes.get(c)
            )
            room = max(0.0, equity * (max_total_pct / 100.0) - invested)
            need_value = min(need_value, room, float(account["cash"]) * 0.995)
            buy_shares = _floor_lot(need_value / px, self.config.lot_size)
            if buy_shares <= 0:
                fills.append(
                    _skip_fill(run_id, run_date, code, item["action"], "现金或总仓不足，未开仓")
                )
                continue
            fill = self._execute_buy(
                run_id, run_date, code, buy_shares, px, item["action"], account, positions
            )
            if fill:
                fills.append(fill)
                equity = self._equity(account["cash"], positions, quotes)

        self.db.sim_save_account(float(account["cash"]))
        self.db.sim_replace_positions(list(positions.values()))
        for f in fills:
            if f.get("skipped"):
                continue
            self.db.sim_insert_fill(f)

        snap = self._mark_snapshot(
            run_id=run_id,
            run_date=run_date,
            account=account,
            positions=positions,
            fills=fills,
            quotes=quotes,
            persist=True,
        )
        log.info(
            "sim apply date=%s equity=%.2f cash=%.2f return=%.2f%% fills=%s",
            run_date,
            snap.get("equity"),
            snap.get("cash"),
            snap.get("nav_return_pct"),
            len([f for f in fills if not f.get("skipped")]),
        )
        return snap

    def _rewind_if_rerun(self, run_date: str) -> None:
        existing = self.db.sim_get_snapshot(run_date)
        if not existing:
            return
        prev = self.db.sim_get_snapshot_before(run_date)
        if prev:
            self.db.sim_restore_snapshot(prev)
        else:
            self.db.sim_reset(self.config.initial_cash)
        self.db.sim_delete_snapshot_and_fills(run_date)
        log.info("sim rewind for re-run date=%s", run_date)

    def _execute_buy(
        self,
        run_id: int,
        run_date: str,
        code: str,
        shares: int,
        price: float,
        action: str,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        amount = round(shares * price, 2)
        fee = _fee_buy(amount)
        total = amount + fee
        if total > float(account["cash"]) + 1e-6:
            affordable = _floor_lot(
                (float(account["cash"]) - 1) / (price * (1 + _COMMISSION_RATE)),
                self.config.lot_size,
            )
            if affordable <= 0:
                return _skip_fill(run_id, run_date, code, action, "现金不足")
            shares = affordable
            amount = round(shares * price, 2)
            fee = _fee_buy(amount)
            total = amount + fee
        account["cash"] = round(float(account["cash"]) - total, 2)
        pos = positions.get(code)
        now = datetime.now().isoformat(timespec="seconds")
        if not pos:
            positions[code] = {
                "stock_code": code,
                "shares": float(shares),
                "avg_cost": price,
                "opened_at": run_date,
                "updated_at": now,
            }
        else:
            old_sh = float(pos["shares"])
            old_cost = float(pos["avg_cost"])
            new_sh = old_sh + shares
            pos["avg_cost"] = (
                round((old_sh * old_cost + shares * price) / new_sh, 4) if new_sh else price
            )
            pos["shares"] = float(new_sh)
            pos["updated_at"] = now
        return {
            "run_id": run_id,
            "run_date": run_date,
            "stock_code": code,
            "side": "buy",
            "shares": float(shares),
            "price": price,
            "amount": amount,
            "cost_fee": fee,
            "action_src": action,
            "note": "",
            "skipped": False,
        }

    def _execute_sell(
        self,
        run_id: int,
        run_date: str,
        code: str,
        shares: float,
        price: float,
        action: str,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        pos = positions.get(code)
        if not pos:
            return None
        shares = min(float(shares), float(pos["shares"]))
        shares = float(_floor_lot(shares, self.config.lot_size))
        if shares <= 0 and float(pos["shares"]) > 0:
            shares = float(pos["shares"])
        if shares <= 0:
            return None
        amount = round(shares * price, 2)
        fee = _fee_sell(amount)
        account["cash"] = round(float(account["cash"]) + amount - fee, 2)
        left = float(pos["shares"]) - shares
        now = datetime.now().isoformat(timespec="seconds")
        if left <= 1e-9:
            positions.pop(code, None)
        else:
            pos["shares"] = left
            pos["updated_at"] = now
        return {
            "run_id": run_id,
            "run_date": run_date,
            "stock_code": code,
            "side": "sell",
            "shares": shares,
            "price": price,
            "amount": amount,
            "cost_fee": fee,
            "action_src": action,
            "note": "",
            "skipped": False,
        }

    def _equity(
        self,
        cash: float,
        positions: dict[str, dict[str, Any]],
        quotes: dict[str, float | None],
    ) -> float:
        total = float(cash)
        for code, pos in positions.items():
            px = quotes.get(code)
            if px is None:
                px = float(pos.get("avg_cost") or 0)
            total += float(pos["shares"]) * float(px)
        return round(total, 2)

    def _mark_snapshot(
        self,
        *,
        run_id: int | None,
        run_date: str | None,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]] | list[dict[str, Any]],
        fills: list[dict[str, Any]],
        quotes: dict[str, float | None],
        persist: bool,
    ) -> dict[str, Any]:
        if isinstance(positions, list):
            pos_map = {p["stock_code"]: p for p in positions}
        else:
            pos_map = positions
        cash = float(account["cash"])
        initial = float(account.get("initial_cash") or self.config.initial_cash)
        pos_rows: list[dict[str, Any]] = []
        mtm = 0.0
        for code, pos in pos_map.items():
            px = quotes.get(code)
            mark = float(px) if px is not None else float(pos.get("avg_cost") or 0)
            sh = float(pos["shares"])
            value = round(sh * mark, 2)
            cost = float(pos.get("avg_cost") or 0)
            pnl_pct = round((mark - cost) / cost * 100, 2) if cost else None
            pos_rows.append(
                {
                    "code": code,
                    "shares": sh,
                    "avg_cost": cost,
                    "mark": mark,
                    "value": value,
                    "pnl_pct": pnl_pct,
                    "opened_at": pos.get("opened_at"),
                }
            )
            mtm += value
        equity = round(cash + mtm, 2)
        for row in pos_rows:
            row["weight_pct"] = round(row["value"] / equity * 100, 2) if equity else 0.0
        nav_return_pct = round((equity - initial) / initial * 100, 2) if initial else 0.0
        snap = {
            "run_id": run_id,
            "run_date": run_date,
            "initial_cash": initial,
            "cash": cash,
            "equity": equity,
            "market_value": round(mtm, 2),
            "nav_return_pct": nav_return_pct,
            "positions": pos_rows,
            "fills": fills,
            "fill_count": len([f for f in fills if not f.get("skipped")]),
        }
        if persist and run_date:
            self.db.sim_upsert_snapshot(snap)
        return snap


def _norm_code(code: Any) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _target_pct(rec: dict[str, Any], default: float | None) -> float | None:
    raw = rec.get("position_pct")
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _skip_fill(
    run_id: int, run_date: str, code: str, action: str, note: str
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "run_date": run_date,
        "stock_code": code,
        "side": "skip",
        "shares": 0,
        "price": 0,
        "amount": 0,
        "cost_fee": 0,
        "action_src": action,
        "note": note,
        "skipped": True,
    }


def render_sim_section(sim: dict[str, Any] | None) -> list[str]:
    """报告附录：折叠展示，避免紧挨 §4 被当成真实持仓。"""
    if not sim or sim.get("skipped"):
        return []
    lines = [
        "<details>",
        "<summary><strong>附录：模拟账本（评估用 · 非真实持仓）</strong></summary>",
        "",
        "> **不是你的账户。** 决策完成后机械回放「若完全按 §4 执行」的效果；"
        "不反向影响建议。缺 `position_pct` 时**不会**静默按默认比例开仓。",
        "",
        f"- **初始资金**: {sim.get('initial_cash'):,.0f} 元"
        if sim.get("initial_cash") is not None
        else "- **初始资金**: —",
        f"- **模拟总权益**: {sim.get('equity'):,.2f} 元 · 现金 {sim.get('cash'):,.2f} · 市值 {sim.get('market_value'):,.2f}",
        f"- **相对初始盈亏**: {sim.get('nav_return_pct')}%",
        "",
    ]
    positions = sim.get("positions") or []
    if positions:
        lines.append("### 模拟持仓（非真实）")
        lines.append("")
        for p in positions:
            lines.append(
                f"- `{p.get('code')}` {p.get('shares'):.0f}股 · 成本 {p.get('avg_cost')} · "
                f"现价 {p.get('mark')} · 市值 {p.get('value'):,.2f} · "
                f"浮盈亏 {p.get('pnl_pct')}% · 仓位 {p.get('weight_pct')}%"
            )
        lines.append("")
    else:
        lines.append("_模拟盘当前空仓（现金待命）_")
        lines.append("")
    fills = [f for f in (sim.get("fills") or []) if not f.get("skipped")]
    skips = [f for f in (sim.get("fills") or []) if f.get("skipped")]
    if fills:
        lines.append("### 本轮模拟成交")
        lines.append("")
        for f in fills:
            lines.append(
                f"- {f.get('side')} `{f.get('stock_code')}` {f.get('shares'):.0f}股 @ {f.get('price')} "
                f"（执行报告动作 `{f.get('action_src')}`，费用 {f.get('cost_fee')}）"
            )
        lines.append("")
    if skips:
        lines.append("### 本轮未成交")
        lines.append("")
        for f in skips:
            lines.append(f"- `{f.get('stock_code')}` [{f.get('action_src')}] {f.get('note')}")
        lines.append("")
    lines.append(
        "_真实持仓只看 `config.yaml` → `holdings`（未声明=空仓）；"
        "`watch_stocks` 是必跟研究名单，不是持仓。_"
    )
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return lines
