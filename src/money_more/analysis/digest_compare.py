"""对比多日 decision_digest，观察建议稳定性。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_digests(digests_dir: Path, limit: int = 10) -> list[dict[str, Any]]:
    if not digests_dir.exists():
        return []
    files = sorted(digests_dir.glob("*.json"))[-limit:]
    out = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def compare_digests(digests: list[dict[str, Any]]) -> dict[str, Any]:
    if len(digests) < 2:
        return {"ok": False, "reason": "需要至少 2 日 digest", "n": len(digests)}
    a, b = digests[-2], digests[-1]
    phase_flip = a.get("market_phase") != b.get("market_phase")
    codes_a = {r.get("code"): r for r in a.get("recommendations") or []}
    codes_b = {r.get("code"): r for r in b.get("recommendations") or []}
    flips = []
    for code in set(codes_a) | set(codes_b):
        aa = (codes_a.get(code) or {}).get("action")
        bb = (codes_b.get(code) or {}).get("action")
        if aa and bb and aa != bb:
            flips.append({"code": code, "from": aa, "to": bb})
    return {
        "ok": True,
        "from_date": a.get("run_date"),
        "to_date": b.get("run_date"),
        "phase_flip": phase_flip,
        "phase": f"{a.get('market_phase')}→{b.get('market_phase')}",
        "action_flips": flips,
        "dq": f"{a.get('data_quality_score')}→{b.get('data_quality_score')}",
    }
