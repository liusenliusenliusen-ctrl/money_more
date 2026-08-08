#!/usr/bin/env python3
"""第五波 C2：影子决策回放（只读对比，不改决策、不进生产路径）。

对历史 digest 里的 recommendations 重新套用**当前** validator 门禁
（framework gates / 景气 / 矛盾 / 硬共振口径取自 digest 缺失时按中性），
输出「若用现在的规则，当时的建议会怎么变」的对照表。

用途：
- 检验新闸是否过度封买（历史 buy 是否会被大面积翻成 watch）
- 检验新闸是否能拦下历史 miss

用法:
  .venv/bin/python scripts/shadow_replay.py
  .venv/bin/python scripts/shadow_replay.py --days 120
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))

from money_more.analysis.decision_validator import validate_recommendations  # noqa: E402


def _load_digests(digests_dir: Path, days: int) -> list[dict[str, Any]]:
    from datetime import date, timedelta

    cutoff = date.today() - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for p in sorted(digests_dir.glob("????-??-??.json")):
        try:
            from datetime import datetime

            d0 = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d0 < cutoff:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["_date"] = p.stem
            out.append(d)
        except Exception:
            continue
    return out


def replay_one(digest: dict[str, Any]) -> dict[str, Any]:
    """对单个 digest 重跑 validator（中性 gates：digest 未存 gates 时不强行封）。"""
    recs = [dict(r) for r in (digest.get("recommendations") or []) if r.get("code")]
    if not recs:
        return {"date": digest.get("_date"), "changed": 0, "total": 0, "flips": []}
    # digest 里没有 framework_gates 快照；用中性闸（不额外封），只跑通用校验
    out, overrides = validate_recommendations(
        recs,
        holdings=[],
        constraints={
            "max_single_position_pct": 20,
            "max_total_position_pct": 80,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
        },
        data_quality={"score": digest.get("data_quality_score") or 1.0},
        market_risk_level=digest.get("risk_level"),
    )
    flips: list[str] = []
    for orig, new in zip(recs, out):
        a0 = str(orig.get("action") or "")
        a1 = str(new.get("action") or "")
        if a0 != a1:
            flips.append(f"{new.get('code')}: {a0}→{a1}")
    return {
        "date": digest.get("_date"),
        "total": len(recs),
        "changed": len(flips),
        "flips": flips,
        "override_count": len(overrides),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Shadow replay of historical recommendations")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--digests-dir", type=Path, default=ROOT / "reports" / "digests")
    args = ap.parse_args()

    digests = _load_digests(args.digests_dir, args.days)
    if not digests:
        print("no digests found")
        return 0
    rows = [replay_one(d) for d in digests]
    tot = sum(r["total"] for r in rows)
    chg = sum(r["changed"] for r in rows)
    print(f"# 影子回放（近 {args.days} 天，{len(rows)} 期）")
    print(f"共 {tot} 条建议；按当前规则会有 {chg} 条动作变化（{round(chg / tot * 100, 1) if tot else 0}%）")
    print()
    for r in rows:
        if r["changed"]:
            print(f"- {r['date']}: {r['changed']}/{r['total']} 变化（多为空仓口径/止损回算所致）")
            for f in r["flips"][:6]:
                print(f"  - {f}")
    print()
    print("注：digest 未存 framework_gates 快照，影子回放只跑通用校验；"
          "用于观察「通用校验本身」会让多少历史动作变化，不代表完整框架闸重放。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
