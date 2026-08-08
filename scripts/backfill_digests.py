#!/usr/bin/env python3
"""从 reports/YYYY-MM-DD.json 回填/加厚 reports/digests/*.json（第二波）。

用法:
  .venv/bin/python scripts/backfill_digests.py
  .venv/bin/python scripts/backfill_digests.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from money_more.analysis.decision_digest import build_decision_digest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill decision digests from report JSON")
    ap.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    reports_dir: Path = args.reports_dir
    digests_dir = reports_dir / "digests"
    digests_dir.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    n_skip = 0
    for path in sorted(reports_dir.glob("????-??-??.json")):
        if path.stat().st_size < 500:
            n_skip += 1
            continue
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"skip {path.name}: {exc}")
            n_skip += 1
            continue
        if not result.get("run_date"):
            result["run_date"] = path.stem
        # 瘦包（仅门禁失败）无 market/sectors 时仍写最小 digest
        digest = build_decision_digest(result)
        out = digests_dir / f"{path.stem}.json"
        if args.dry_run:
            print(f"would write {out} keys={list(digest.keys())[:8]}…")
        else:
            out.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"wrote {out.name}")
        n_ok += 1
    print(f"done ok={n_ok} skip={n_skip} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
