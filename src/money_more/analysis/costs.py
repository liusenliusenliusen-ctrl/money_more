"""A 股纸面交易成本粗算。"""

from __future__ import annotations


def apply_ashare_costs(gross_return_pct: float, side: str = "roundtrip") -> float:
    """粗略 A 股成本：佣金万三双边 + 印花税卖出 0.05%（单向）。单位：百分比点数。"""
    commission_one_way = 0.03
    stamp = 0.05
    if side == "entry":
        return round(gross_return_pct - commission_one_way, 4)
    return round(gross_return_pct - commission_one_way * 2 - stamp, 4)
