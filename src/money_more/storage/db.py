from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from money_more.utils.json_util import dumps_json


SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    report_path TEXT
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    analysis_json TEXT,
    FOREIGN KEY (run_id) REFERENCES daily_runs(id)
);

CREATE TABLE IF NOT EXISTS sector_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    sector_name TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    analysis_json TEXT,
    FOREIGN KEY (run_id) REFERENCES daily_runs(id)
);

CREATE TABLE IF NOT EXISTS stock_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stock_code TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    analysis_json TEXT,
    FOREIGN KEY (run_id) REFERENCES daily_runs(id)
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stock_code TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL,
    target_price REAL,
    stop_loss REAL,
    position_pct REAL,
    rationale TEXT,
    extra_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES daily_runs(id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    recommendation_id INTEGER NOT NULL,
    stock_code TEXT NOT NULL,
    original_action TEXT NOT NULL,
    outcome TEXT,
    return_pct REAL,
    diagnosis TEXT,
    diagnosis_category TEXT,
    lesson TEXT,
    extra_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES daily_runs(id),
    FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source_review_id INTEGER,
    weight REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    FOREIGN KEY (source_review_id) REFERENCES reviews(id)
);

CREATE TABLE IF NOT EXISTS trend_reports (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    report_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_date TEXT
);

CREATE TABLE IF NOT EXISTS intelligence_digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL UNIQUE,
    digest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES daily_runs(id)
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER,
    stock_code TEXT NOT NULL,
    action TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL,
    target_price REAL,
    position_pct REAL,
    status TEXT NOT NULL DEFAULT 'open',
    current_price REAL,
    return_pct REAL,
    max_dd_pct REAL,
    exit_date TEXT,
    exit_price REAL,
    exit_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
);

CREATE TABLE IF NOT EXISTS sim_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    initial_cash REAL NOT NULL,
    cash REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_positions (
    stock_code TEXT PRIMARY KEY,
    shares REAL NOT NULL,
    avg_cost REAL NOT NULL,
    opened_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    run_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    side TEXT NOT NULL,
    shares REAL NOT NULL,
    price REAL NOT NULL,
    amount REAL NOT NULL,
    cost_fee REAL NOT NULL,
    action_src TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    run_date TEXT NOT NULL UNIQUE,
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    nav_return_pct REAL,
    positions_json TEXT NOT NULL,
    fills_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

MIGRATIONS = [
    "ALTER TABLE recommendations ADD COLUMN extra_json TEXT",
    "ALTER TABLE reviews ADD COLUMN diagnosis_category TEXT",
    "ALTER TABLE reviews ADD COLUMN extra_json TEXT",
]

# 在建唯一索引前先去重（历史数据可能一对多）
POST_MIGRATIONS = [
    """
    DELETE FROM reviews
    WHERE id NOT IN (
        SELECT MAX(id) FROM reviews GROUP BY recommendation_id
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_recommendation_id ON reviews(recommendation_id)",
]


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            for sql in MIGRATIONS:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
            for sql in POST_MIGRATIONS:
                try:
                    conn.execute(sql)
                except (sqlite3.OperationalError, sqlite3.IntegrityError):
                    pass
            conn.commit()

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fail_stuck_runs(self, max_hours: int = 6) -> int:
        """将超时仍为 running 的任务标记为 failed。"""
        with self.session() as conn:
            cur = conn.execute(
                """
                UPDATE daily_runs
                SET status='failed', finished_at=datetime('now')
                WHERE status='running'
                  AND started_at < datetime('now', ?)
                """,
                (f"-{int(max_hours)} hours",),
            )
            return int(cur.rowcount or 0)

    def ensure_run(self, run_date: date, mode: str = "review") -> int:
        """复盘等轻量模式：不删除当日快照。"""
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            existing = conn.execute(
                "SELECT id FROM daily_runs WHERE run_date = ?", (run_date.isoformat(),)
            ).fetchone()
            if existing:
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO daily_runs (run_date, status, started_at)
                VALUES (?, ?, ?)
                """,
                (run_date.isoformat(), mode, now),
            )
            return int(cur.lastrowid)

    def start_run(self, run_date: date) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            existing = conn.execute(
                "SELECT id FROM daily_runs WHERE run_date = ?", (run_date.isoformat(),)
            ).fetchone()
            if existing:
                run_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE daily_runs
                    SET status='running', started_at=?, finished_at=NULL, report_path=NULL
                    WHERE id=?
                    """,
                    (now, run_id),
                )
                # 同日重跑：清理旧快照与当日建议，避免趋势序列重复
                for table in (
                    "market_snapshots",
                    "sector_snapshots",
                    "stock_snapshots",
                    "intelligence_digests",
                ):
                    conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
                # 仅删除尚未复盘的当日建议
                conn.execute(
                    """
                    DELETE FROM recommendations
                    WHERE run_id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM reviews rv WHERE rv.recommendation_id = recommendations.id
                      )
                    """,
                    (run_id,),
                )
                return run_id

            cur = conn.execute(
                """
                INSERT INTO daily_runs (run_date, status, started_at)
                VALUES (?, 'running', ?)
                """,
                (run_date.isoformat(), now),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, report_path: str | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            conn.execute(
                """
                UPDATE daily_runs
                SET status = ?, finished_at = ?, report_path = ?
                WHERE id = ?
                """,
                (status, now, report_path, run_id),
            )

    def save_market_snapshot(self, run_id: int, snapshot: dict, analysis: dict | None) -> None:
        with self.session() as conn:
            conn.execute("DELETE FROM market_snapshots WHERE run_id = ?", (run_id,))
            conn.execute(
                """
                INSERT INTO market_snapshots (run_id, snapshot_json, analysis_json)
                VALUES (?, ?, ?)
                """,
                (run_id, dumps_json(snapshot), dumps_json(analysis or {})),
            )

    def save_sector_snapshot(
        self, run_id: int, sector: str, snapshot: dict, analysis: dict | None
    ) -> None:
        with self.session() as conn:
            conn.execute(
                "DELETE FROM sector_snapshots WHERE run_id = ? AND sector_name = ?",
                (run_id, sector),
            )
            conn.execute(
                """
                INSERT INTO sector_snapshots (run_id, sector_name, snapshot_json, analysis_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, sector, dumps_json(snapshot), dumps_json(analysis or {})),
            )

    def save_stock_snapshot(
        self, run_id: int, code: str, snapshot: dict, analysis: dict | None
    ) -> None:
        with self.session() as conn:
            conn.execute(
                "DELETE FROM stock_snapshots WHERE run_id = ? AND stock_code = ?",
                (run_id, code),
            )
            conn.execute(
                """
                INSERT INTO stock_snapshots (run_id, stock_code, snapshot_json, analysis_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, code, dumps_json(snapshot), dumps_json(analysis or {})),
            )

    def save_intelligence_digest(self, run_id: int, digest: dict) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO intelligence_digests (run_id, digest_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    digest_json=excluded.digest_json,
                    created_at=excluded.created_at
                """,
                (run_id, dumps_json(digest), now),
            )

    def save_recommendation(
        self,
        run_id: int,
        stock_code: str,
        action: str,
        confidence: float | None,
        target_price: float | None,
        stop_loss: float | None,
        position_pct: float | None,
        rationale: str,
        extra: dict | None = None,
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            cur = conn.execute(
                """
                INSERT INTO recommendations
                (run_id, stock_code, action, confidence, target_price, stop_loss,
                 position_pct, rationale, extra_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    stock_code,
                    action,
                    confidence,
                    target_price,
                    stop_loss,
                    position_pct,
                    rationale,
                    dumps_json(extra or {}),
                    now,
                ),
            )
            return int(cur.lastrowid)

    def save_review(
        self,
        run_id: int,
        recommendation_id: int,
        stock_code: str,
        original_action: str,
        outcome: str,
        return_pct: float | None,
        diagnosis: str,
        lesson: str,
        diagnosis_category: str | None = None,
        extra: dict | None = None,
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            existing = conn.execute(
                "SELECT id FROM reviews WHERE recommendation_id = ?",
                (recommendation_id,),
            ).fetchone()
            if existing:
                review_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE reviews
                    SET run_id=?, stock_code=?, original_action=?, outcome=?,
                        return_pct=?, diagnosis=?, diagnosis_category=?, lesson=?,
                        extra_json=?, created_at=?
                    WHERE id=?
                    """,
                    (
                        run_id,
                        stock_code,
                        original_action,
                        outcome,
                        return_pct,
                        diagnosis,
                        diagnosis_category,
                        lesson,
                        dumps_json(extra or {}),
                        now,
                        review_id,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO reviews
                    (run_id, recommendation_id, stock_code, original_action, outcome,
                     return_pct, diagnosis, diagnosis_category, lesson, extra_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        recommendation_id,
                        stock_code,
                        original_action,
                        outcome,
                        return_pct,
                        diagnosis,
                        diagnosis_category,
                        lesson,
                        dumps_json(extra or {}),
                        now,
                    ),
                )
                review_id = int(cur.lastrowid)
            # 经验去重入库放在 pipeline.insert_lesson_if_new；此处仅新插入时写一条
            if not existing and lesson.strip():
                conn.execute(
                    """
                    INSERT INTO lessons (category, content, source_review_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("review", lesson.strip(), review_id, now),
                )
            return review_id

    def get_active_lessons(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT category, content, weight, created_at
                FROM lessons
                WHERE active = 1
                ORDER BY weight DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recommendations_for_review(self, before_date: date, lookback_days: int) -> list[dict[str, Any]]:
        """待复盘建议：无复盘，或仅有 tracking/pending/thesis_intact（可再跟踪）。"""
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.stock_code, r.action, r.confidence, r.target_price, r.stop_loss,
                       r.position_pct, r.rationale, r.extra_json, r.created_at, d.run_date
                FROM recommendations r
                JOIN daily_runs d ON d.id = r.run_id
                WHERE d.run_date <= ?
                  AND d.run_date >= date(?, '-' || ? || ' days')
                  AND (
                      NOT EXISTS (
                          SELECT 1 FROM reviews rv WHERE rv.recommendation_id = r.id
                      )
                      OR EXISTS (
                          SELECT 1 FROM reviews rv
                          WHERE rv.recommendation_id = r.id
                            AND lower(COALESCE(rv.outcome, '')) IN (
                                'tracking', 'pending', 'thesis_intact'
                            )
                      )
                  )
                ORDER BY r.created_at ASC
                """,
                (before_date.isoformat(), before_date.isoformat(), lookback_days),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_recommendations(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT r.*, d.run_date
                FROM recommendations r
                JOIN daily_runs d ON d.id = r.run_id
                ORDER BY r.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_market_analysis_series(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT d.run_date, m.analysis_json
                FROM market_snapshots m
                JOIN daily_runs d ON d.id = m.run_id
                JOIN (
                    SELECT run_id, MAX(id) AS mid
                    FROM market_snapshots
                    GROUP BY run_id
                ) latest ON latest.mid = m.id
                WHERE d.status = 'success'
                ORDER BY d.run_date DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in reversed(rows):
            analysis = json.loads(r["analysis_json"] or "{}")
            out.append({"run_date": r["run_date"], "analysis": analysis})
        return out

    def get_sector_analysis_series(self, sector: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT d.run_date, s.analysis_json
                FROM sector_snapshots s
                JOIN daily_runs d ON d.id = s.run_id
                WHERE d.status = 'success' AND s.sector_name = ?
                ORDER BY d.run_date DESC
                LIMIT ?
                """,
                (sector, limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in reversed(rows):
            out.append({"run_date": r["run_date"], "analysis": json.loads(r["analysis_json"] or "{}")})
        return out

    def get_stock_analysis_series(self, code: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT d.run_date, s.analysis_json
                FROM stock_snapshots s
                JOIN daily_runs d ON d.id = s.run_id
                WHERE d.status = 'success' AND s.stock_code = ?
                ORDER BY d.run_date DESC
                LIMIT ?
                """,
                (code, limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in reversed(rows):
            out.append({"run_date": r["run_date"], "analysis": json.loads(r["analysis_json"] or "{}")})
        return out

    def get_prior_context(self, limit: int = 5) -> dict[str, Any]:
        """供 LLM 做跨日一致性判断的精简历史。"""
        series = self.get_market_analysis_series(limit=limit)
        compact = []
        for item in series:
            a = item.get("analysis") or {}
            sent = a.get("sentiment_assessment") or {}
            liq = a.get("liquidity_assessment") or {}
            compact.append(
                {
                    "date": item["run_date"],
                    "phase": a.get("phase"),
                    "phase_label": a.get("phase_label"),
                    "style": a.get("style"),
                    "risk_level": a.get("risk_level"),
                    "primary_driver": a.get("primary_driver"),
                    "sector_allocation_hint": a.get("sector_allocation_hint"),
                    "sentiment_level": sent.get("level"),
                    "quant_score_100": sent.get("quant_score_100"),
                    "margin_trend": liq.get("margin_trend"),
                    "confidence": a.get("confidence"),
                }
            )
        return {"market_history": compact, "days": len(compact)}

    def get_analysis_at_date(self, run_date: str, stock_code: str) -> dict[str, Any]:
        """复盘用：拉取建议发出当日的市场/个股分析与情报摘要。"""
        code = "".join(ch for ch in str(stock_code) if ch.isdigit())[-6:].zfill(6)
        out: dict[str, Any] = {"run_date": run_date, "stock_code": code}
        with self.session() as conn:
            run = conn.execute(
                "SELECT id, report_path FROM daily_runs WHERE run_date = ? AND status = 'success'",
                (run_date,),
            ).fetchone()
            if not run:
                run = conn.execute(
                    "SELECT id, report_path FROM daily_runs WHERE run_date = ?",
                    (run_date,),
                ).fetchone()
            if not run:
                out["note"] = "no_run_for_date"
                return out
            run_id = int(run["id"])
            out["report_path"] = run["report_path"]

            m = conn.execute(
                "SELECT analysis_json FROM market_snapshots WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if m and m["analysis_json"]:
                ma = json.loads(m["analysis_json"])
                sent = ma.get("sentiment_assessment") or {}
                out["market"] = {
                    "phase": ma.get("phase"),
                    "phase_label": ma.get("phase_label"),
                    "style": ma.get("style"),
                    "risk_level": ma.get("risk_level"),
                    "primary_driver": ma.get("primary_driver"),
                    "summary": (ma.get("summary") or "")[:400],
                    "sentiment_level": sent.get("level"),
                    "confidence": ma.get("confidence"),
                }

            s = conn.execute(
                """
                SELECT analysis_json, snapshot_json FROM stock_snapshots
                WHERE run_id = ? AND stock_code = ?
                ORDER BY id DESC LIMIT 1
                """,
                (run_id, code),
            ).fetchone()
            if s and s["analysis_json"]:
                sa = json.loads(s["analysis_json"])
                out["stock_analysis"] = {
                    "summary": (sa.get("summary") or "")[:500],
                    "thesis": sa.get("thesis") or sa.get("investment_thesis"),
                    "action_hint": sa.get("action_hint") or sa.get("suggested_action"),
                    "confidence": sa.get("confidence"),
                    "key_risks": (sa.get("key_risks") or sa.get("risks") or [])[:5],
                    "catalysts": (sa.get("catalysts") or [])[:4],
                    "valuation": sa.get("valuation"),
                    "sentiment": sa.get("sentiment"),
                }

            d = conn.execute(
                "SELECT digest_json FROM intelligence_digests WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if d and d["digest_json"]:
                dig = json.loads(d["digest_json"])
                out["intelligence_digest"] = {
                    "executive_summary": (dig.get("executive_summary") or "")[:400],
                    "headline_themes": (dig.get("headline_themes") or [])[:5],
                    "risk_flags": (dig.get("risk_flags") or [])[:4],
                    "sentiment_temperature": dig.get("sentiment_temperature"),
                }
        return out

    def get_trend_report(self) -> dict[str, Any] | None:
        with self.session() as conn:
            row = conn.execute("SELECT report_json, updated_at, last_run_date FROM trend_reports WHERE id = 1").fetchone()
        if not row:
            return None
        data = json.loads(row["report_json"] or "{}")
        data["_meta"] = {"updated_at": row["updated_at"], "last_run_date": row["last_run_date"]}
        return data

    def save_trend_report(self, report: dict, run_date: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        payload = {k: v for k, v in report.items() if not str(k).startswith("_")}
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO trend_reports (id, report_json, updated_at, last_run_date)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    report_json=excluded.report_json,
                    updated_at=excluded.updated_at,
                    last_run_date=excluded.last_run_date
                """,
                (dumps_json(payload), now, run_date),
            )

    def insert_lesson_if_new(
        self, category: str, content: str, lookback_days: int = 7, source_review_id: int | None = None
    ) -> bool:
        """近 N 天内相同内容不重复写入。"""
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT id FROM lessons
                WHERE category = ? AND content = ?
                  AND created_at >= datetime('now', ?)
                LIMIT 1
                """,
                (category, content, f"-{int(lookback_days)} days"),
            ).fetchone()
            if row:
                return False
            conn.execute(
                """
                INSERT INTO lessons (category, content, source_review_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (category, content, source_review_id, now),
            )
            return True

    def open_paper_trade(
        self,
        recommendation_id: int,
        stock_code: str,
        action: str,
        entry_date: str,
        entry_price: float,
        stop_loss: float | None = None,
        target_price: float | None = None,
        position_pct: float | None = None,
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            cur = conn.execute(
                """
                INSERT INTO paper_trades
                (recommendation_id, stock_code, action, entry_date, entry_price,
                 stop_loss, target_price, position_pct, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    recommendation_id,
                    stock_code,
                    action,
                    entry_date,
                    entry_price,
                    stop_loss,
                    target_price,
                    position_pct,
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def get_open_paper_trades(self) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY id ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_paper_trade(
        self,
        trade_id: int,
        current_price: float | None,
        return_pct: float | None,
        status: str,
        exit_date: str | None = None,
        exit_price: float | None = None,
        exit_reason: str | None = None,
        max_dd_pct: float | None = None,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            conn.execute(
                """
                UPDATE paper_trades
                SET current_price=?, return_pct=?, status=?, exit_date=?, exit_price=?,
                    exit_reason=?, max_dd_pct=?, updated_at=?
                WHERE id=?
                """,
                (
                    current_price,
                    return_pct,
                    status,
                    exit_date,
                    exit_price,
                    exit_reason,
                    max_dd_pct,
                    now,
                    trade_id,
                ),
            )

    def get_paper_trade_stats(self) -> dict[str, Any]:
        with self.session() as conn:
            rows = conn.execute("SELECT * FROM paper_trades").fetchall()
        trades = [dict(r) for r in rows]
        closed = [t for t in trades if t.get("status") == "closed"]
        open_t = [t for t in trades if t.get("status") == "open"]
        rets = [float(t["return_pct"]) for t in closed if t.get("return_pct") is not None]
        hits = [r for r in rets if r > 0]
        by_action: dict[str, list[float]] = {}
        for t in closed:
            if t.get("return_pct") is None:
                continue
            by_action.setdefault(str(t.get("action")), []).append(float(t["return_pct"]))
        return {
            "total": len(trades),
            "open": len(open_t),
            "closed": len(closed),
            "hit_rate": round(len(hits) / len(rets), 3) if rets else None,
            "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
            "avg_by_action": {
                k: round(sum(v) / len(v), 2) for k, v in by_action.items() if v
            },
            "open_marks": [
                {
                    "code": t["stock_code"],
                    "entry": t["entry_price"],
                    "current": t.get("current_price"),
                    "return_pct": t.get("return_pct"),
                }
                for t in open_t
            ],
        }

    # ----- 模拟组合（初始资金评估） -----

    def sim_ensure_account(self, initial_cash: float) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            row = conn.execute("SELECT * FROM sim_account WHERE id = 1").fetchone()
            if row:
                return dict(row)
            conn.execute(
                """
                INSERT INTO sim_account (id, initial_cash, cash, updated_at)
                VALUES (1, ?, ?, ?)
                """,
                (float(initial_cash), float(initial_cash), now),
            )
        return {
            "id": 1,
            "initial_cash": float(initial_cash),
            "cash": float(initial_cash),
            "updated_at": now,
        }

    def sim_get_account(self) -> dict[str, Any] | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM sim_account WHERE id = 1").fetchone()
        return dict(row) if row else None

    def sim_save_account(self, cash: float) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            conn.execute(
                "UPDATE sim_account SET cash=?, updated_at=? WHERE id=1",
                (float(cash), now),
            )

    def sim_reset(self, initial_cash: float) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            conn.execute("DELETE FROM sim_fills")
            conn.execute("DELETE FROM sim_snapshots")
            conn.execute("DELETE FROM sim_positions")
            conn.execute("DELETE FROM sim_account")
            conn.execute(
                """
                INSERT INTO sim_account (id, initial_cash, cash, updated_at)
                VALUES (1, ?, ?, ?)
                """,
                (float(initial_cash), float(initial_cash), now),
            )
        return {
            "id": 1,
            "initial_cash": float(initial_cash),
            "cash": float(initial_cash),
            "updated_at": now,
        }

    def sim_get_positions(self) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM sim_positions ORDER BY stock_code"
            ).fetchall()
        return [dict(r) for r in rows]

    def sim_replace_positions(self, positions: list[dict[str, Any]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            conn.execute("DELETE FROM sim_positions")
            for p in positions:
                conn.execute(
                    """
                    INSERT INTO sim_positions
                    (stock_code, shares, avg_cost, opened_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(p["stock_code"]),
                        float(p["shares"]),
                        float(p["avg_cost"]),
                        str(p.get("opened_at") or now[:10]),
                        str(p.get("updated_at") or now),
                    ),
                )

    def sim_insert_fill(self, fill: dict[str, Any]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            cur = conn.execute(
                """
                INSERT INTO sim_fills
                (run_id, run_date, stock_code, side, shares, price, amount,
                 cost_fee, action_src, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.get("run_id"),
                    str(fill.get("run_date")),
                    str(fill.get("stock_code")),
                    str(fill.get("side")),
                    float(fill.get("shares") or 0),
                    float(fill.get("price") or 0),
                    float(fill.get("amount") or 0),
                    float(fill.get("cost_fee") or 0),
                    fill.get("action_src"),
                    fill.get("note") or "",
                    now,
                ),
            )
            return int(cur.lastrowid)

    def sim_upsert_snapshot(self, snap: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.session() as conn:
            conn.execute("DELETE FROM sim_snapshots WHERE run_date = ?", (snap["run_date"],))
            conn.execute(
                """
                INSERT INTO sim_snapshots
                (run_id, run_date, cash, equity, nav_return_pct,
                 positions_json, fills_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.get("run_id"),
                    snap["run_date"],
                    float(snap["cash"]),
                    float(snap["equity"]),
                    snap.get("nav_return_pct"),
                    dumps_json(snap.get("positions") or []),
                    dumps_json(snap.get("fills") or []),
                    now,
                ),
            )

    def sim_get_snapshot(self, run_date: str) -> dict[str, Any] | None:
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM sim_snapshots WHERE run_date = ?", (run_date,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["positions"] = json.loads(d.get("positions_json") or "[]")
        d["fills"] = json.loads(d.get("fills_json") or "[]")
        return d

    def sim_get_snapshot_before(self, run_date: str) -> dict[str, Any] | None:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT * FROM sim_snapshots
                WHERE run_date < ?
                ORDER BY run_date DESC
                LIMIT 1
                """,
                (run_date,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["positions"] = json.loads(d.get("positions_json") or "[]")
        d["fills"] = json.loads(d.get("fills_json") or "[]")
        return d

    def sim_list_snapshots(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT run_date, cash, equity, nav_return_pct, created_at
                FROM sim_snapshots
                ORDER BY run_date DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    def sim_delete_snapshot_and_fills(self, run_date: str) -> None:
        with self.session() as conn:
            conn.execute("DELETE FROM sim_snapshots WHERE run_date = ?", (run_date,))
            conn.execute("DELETE FROM sim_fills WHERE run_date = ?", (run_date,))

    def sim_restore_snapshot(self, snap: dict[str, Any]) -> None:
        """用某日快照恢复现金与持仓（不含当日 fills）。"""
        now = datetime.now().isoformat(timespec="seconds")
        positions = snap.get("positions") or []
        with self.session() as conn:
            conn.execute(
                "UPDATE sim_account SET cash=?, updated_at=? WHERE id=1",
                (float(snap["cash"]), now),
            )
            conn.execute("DELETE FROM sim_positions")
            for p in positions:
                code = str(p.get("code") or p.get("stock_code") or "")
                if not code:
                    continue
                conn.execute(
                    """
                    INSERT INTO sim_positions
                    (stock_code, shares, avg_cost, opened_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        code,
                        float(p.get("shares") or 0),
                        float(p.get("avg_cost") or p.get("mark") or 0),
                        str(p.get("opened_at") or snap.get("run_date") or now[:10]),
                        now,
                    ),
                )

    def sim_summary(self) -> dict[str, Any]:
        account = self.sim_get_account()
        snaps = self.sim_list_snapshots(limit=1)
        latest = snaps[0] if snaps else None
        with self.session() as conn:
            n_fills = conn.execute("SELECT COUNT(*) AS c FROM sim_fills").fetchone()["c"]
            n_snaps = conn.execute("SELECT COUNT(*) AS c FROM sim_snapshots").fetchone()["c"]
        return {
            "account": account,
            "latest_snapshot": latest,
            "fill_count": int(n_fills),
            "snapshot_count": int(n_snaps),
            "positions": self.sim_get_positions(),
        }
