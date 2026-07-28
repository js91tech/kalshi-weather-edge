from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    city_id TEXT NOT NULL,
    target_date TEXT NOT NULL,
    mu REAL NOT NULL,
    sigma REAL NOT NULL,
    p10 REAL,
    p50 REAL,
    p90 REAL,
    source TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(city_id, target_date, created_at),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS markets_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    city_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    event_ticker TEXT,
    strike_type TEXT,
    floor_strike REAL,
    cap_strike REAL,
    subtitle TEXT,
    yes_bid REAL,
    yes_ask REAL,
    last_price REAL,
    volume REAL,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    created_at TEXT NOT NULL,
    city_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    event_ticker TEXT,
    target_date TEXT,
    action TEXT NOT NULL,
    side TEXT,
    execution TEXT,
    model_p REAL NOT NULL,
    market_mid REAL NOT NULL,
    yes_bid REAL,
    yes_ask REAL,
    spread REAL,
    fee REAL,
    edge REAL NOT NULL,
    suggested_contracts REAL,
    reason TEXT,
    meta_json TEXT,
    -- settlement filled later
    outcome INTEGER,
    settled_at TEXT,
    pnl REAL,
    UNIQUE(ticker, created_at),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS settlements (
    ticker TEXT PRIMARY KEY,
    event_ticker TEXT,
    result TEXT,
    settled_at TEXT,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_city ON signals(city_id);
CREATE INDEX IF NOT EXISTS idx_signals_action ON signals(action);
CREATE INDEX IF NOT EXISTS idx_forecasts_city_date ON forecasts(city_id, target_date);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Ledger:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def start_run(self, notes: str = "") -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, notes) VALUES (?, ?)",
                (utc_now(), notes),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ? WHERE id = ?",
                (utc_now(), run_id),
            )

    def insert_forecast(
        self,
        run_id: int,
        city_id: str,
        target_date: str,
        mu: float,
        sigma: float,
        p10: float | None,
        p50: float | None,
        p90: float | None,
        source: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO forecasts
                (run_id, city_id, target_date, mu, sigma, p10, p50, p90, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, city_id, target_date, mu, sigma, p10, p50, p90, source, utc_now()),
            )

    def insert_market(self, run_id: int, city_id: str, market: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO markets_snapshot
                (run_id, city_id, ticker, event_ticker, strike_type, floor_strike, cap_strike,
                 subtitle, yes_bid, yes_ask, last_price, volume, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    city_id,
                    market.get("ticker"),
                    market.get("event_ticker"),
                    market.get("strike_type"),
                    market.get("floor_strike"),
                    market.get("cap_strike"),
                    market.get("subtitle") or market.get("no_sub_title"),
                    _dollar(market.get("yes_bid_dollars")),
                    _dollar(market.get("yes_ask_dollars")),
                    _dollar(market.get("last_price_dollars")),
                    _float(market.get("volume_fp")),
                    json.dumps(market),
                    utc_now(),
                ),
            )

    def insert_signal(self, run_id: int, signal: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO signals
                (run_id, created_at, city_id, ticker, event_ticker, target_date, action, side,
                 execution, model_p, market_mid, yes_bid, yes_ask, spread, fee, edge,
                 suggested_contracts, reason, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    signal.get("created_at") or utc_now(),
                    signal["city_id"],
                    signal["ticker"],
                    signal.get("event_ticker"),
                    signal.get("target_date"),
                    signal["action"],
                    signal.get("side"),
                    signal.get("execution"),
                    signal["model_p"],
                    signal["market_mid"],
                    signal.get("yes_bid"),
                    signal.get("yes_ask"),
                    signal.get("spread"),
                    signal.get("fee"),
                    signal["edge"],
                    signal.get("suggested_contracts"),
                    signal.get("reason"),
                    json.dumps(signal.get("meta") or {}),
                ),
            )

    def latest_signals(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM signals
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_forecasts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.* FROM forecasts f
                INNER JOIN (
                    SELECT city_id, target_date, MAX(id) AS max_id
                    FROM forecasts
                    GROUP BY city_id, target_date
                ) t ON f.id = t.max_id
                ORDER BY f.city_id, f.target_date
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def signal_stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"]
            trades = conn.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE action != 'PASS'"
            ).fetchone()["n"]
            passes = conn.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE action = 'PASS'"
            ).fetchone()["n"]
            settled = conn.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE outcome IS NOT NULL"
            ).fetchone()["n"]
            pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) AS s FROM signals WHERE pnl IS NOT NULL"
            ).fetchone()["s"]
            wins = conn.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE outcome = 1"
            ).fetchone()["n"]
            losses = conn.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE outcome = 0"
            ).fetchone()["n"]
            open_trades = conn.execute(
                """
                SELECT COUNT(*) AS n FROM signals
                WHERE action != 'PASS' AND outcome IS NULL
                """
            ).fetchone()["n"]
        decided = wins + losses
        return {
            "total_signals": total,
            "trade_signals": trades,
            "pass_signals": passes,
            "settled": settled,
            "open_trades": open_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / decided) if decided else 0.0,
            "paper_pnl": pnl,
        }

    def unsettled_trade_tickers(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT ticker, city_id, event_ticker, meta_json
                FROM signals
                WHERE action != 'PASS' AND outcome IS NULL
                """
            ).fetchall()
        out = []
        for r in rows:
            meta = {}
            try:
                meta = json.loads(r["meta_json"] or "{}")
            except Exception:
                meta = {}
            out.append(
                {
                    "ticker": r["ticker"],
                    "city_id": r["city_id"],
                    "event_ticker": r["event_ticker"],
                    "meta": meta,
                }
            )
        return out

    def performance_board(self, limit: int = 200) -> dict[str, Any]:
        """Settled trade W/L board + equity curve points."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, settled_at, city_id, ticker, action, side,
                       market_mid, outcome, pnl, meta_json
                FROM signals
                WHERE outcome IS NOT NULL AND action != 'PASS'
                ORDER BY COALESCE(settled_at, created_at) ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        equity = 0.0
        curve: list[dict[str, Any]] = []
        by_series: dict[str, dict[str, Any]] = {}
        trades = []
        for r in rows:
            row = dict(r)
            meta = {}
            try:
                meta = json.loads(row.get("meta_json") or "{}")
            except Exception:
                meta = {}
            series = meta.get("series") or row.get("city_id") or "unknown"
            pnl = float(row.get("pnl") or 0)
            equity += pnl
            curve.append(
                {
                    "t": row.get("settled_at") or row.get("created_at"),
                    "equity": equity,
                    "pnl": pnl,
                    "ticker": row.get("ticker"),
                }
            )
            bucket = by_series.setdefault(
                series, {"series": series, "wins": 0, "losses": 0, "pnl": 0.0, "n": 0}
            )
            bucket["n"] += 1
            bucket["pnl"] += pnl
            if row.get("outcome") == 1:
                bucket["wins"] += 1
            else:
                bucket["losses"] += 1
            trades.append(
                {
                    "settled_at": row.get("settled_at"),
                    "series": series,
                    "ticker": row.get("ticker"),
                    "action": row.get("action"),
                    "side": row.get("side"),
                    "mid": row.get("market_mid"),
                    "won": bool(row.get("outcome") == 1),
                    "pnl": pnl,
                    "strategy": meta.get("strategy"),
                }
            )

        series_rows = []
        for b in by_series.values():
            n = b["wins"] + b["losses"]
            b["win_rate"] = (b["wins"] / n) if n else 0.0
            series_rows.append(b)
        series_rows.sort(key=lambda x: x["pnl"], reverse=True)

        return {
            "equity_curve": curve,
            "by_series": series_rows,
            "trades": list(reversed(trades[-100:])),
            "final_equity": equity,
        }

    def upsert_settlement(self, market: dict[str, Any]) -> None:
        result = market.get("result") or ""
        if not result:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settlements (ticker, event_ticker, result, settled_at, raw_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    result=excluded.result,
                    settled_at=excluded.settled_at,
                    raw_json=excluded.raw_json
                """,
                (
                    market["ticker"],
                    market.get("event_ticker"),
                    result,
                    utc_now(),
                    json.dumps(market),
                ),
            )

    def apply_settlements_to_signals(self) -> int:
        """Fill outcome/pnl for open paper signals when settlement known."""
        updated = 0
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.side, s.execution, s.yes_bid, s.yes_ask, s.suggested_contracts,
                       s.fee, st.result
                FROM signals s
                JOIN settlements st ON st.ticker = s.ticker
                WHERE s.outcome IS NULL AND s.action != 'PASS' AND st.result IN ('yes', 'no')
                """
            ).fetchall()
            for row in rows:
                won = (row["result"] == "yes" and row["side"] == "YES") or (
                    row["result"] == "no" and row["side"] == "NO"
                )
                outcome = 1 if won else 0
                contracts = float(row["suggested_contracts"] or 0)
                # Maker assumption: buy at bid for YES, or buy NO at (1-ask)
                if row["side"] == "YES":
                    entry = float(row["yes_bid"] or 0)
                    pnl = contracts * ((1.0 - entry) if won else (-entry))
                else:
                    # Buying NO approx at (1 - yes_ask) when fading YES as maker on ask side
                    entry = 1.0 - float(row["yes_ask"] or 1)
                    pnl = contracts * ((1.0 - entry) if won else (-entry))
                fee = float(row["fee"] or 0) * contracts
                if (row["execution"] or "").lower() == "taker":
                    pnl -= fee
                conn.execute(
                    """
                    UPDATE signals
                    SET outcome = ?, settled_at = ?, pnl = ?
                    WHERE id = ?
                    """,
                    (outcome, utc_now(), pnl, row["id"]),
                )
                updated += 1
        return updated


def _dollar(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


def _float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)
