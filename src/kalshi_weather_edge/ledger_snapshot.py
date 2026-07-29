from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Ledger, utc_now


def default_snapshot_path(data_dir: Path) -> Path:
    return data_dir / "ledger_snapshot.json"


def export_ledger_snapshot(ledger: Ledger, path: Path) -> dict[str, Any]:
    """Write signals + settlements to JSON for Cloud persistence / backup."""
    with ledger.connect() as conn:
        signals = [dict(r) for r in conn.execute("SELECT * FROM signals ORDER BY id").fetchall()]
        settlements = [
            dict(r) for r in conn.execute("SELECT * FROM settlements ORDER BY ticker").fetchall()
        ]
    payload = {
        "exported_at": utc_now(),
        "version": 1,
        "stats": ledger.signal_stats(),
        "signals": signals,
        "settlements": settlements,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def import_ledger_snapshot(
    ledger: Ledger,
    path: Path,
    *,
    merge: bool = True,
) -> dict[str, Any]:
    """
    Load snapshot into SQLite. When merge=True, only adds missing rows
    (by signal ticker+created_at, settlement ticker).
    """
    if not path.exists():
        return {"ok": False, "skipped": True, "reason": "snapshot missing"}

    payload = json.loads(path.read_text(encoding="utf-8"))
    imported_signals = 0
    imported_settlements = 0
    skipped_signals = 0

    with ledger.connect() as conn:
        existing_signal_keys = set()
        if merge:
            for row in conn.execute("SELECT ticker, created_at FROM signals").fetchall():
                existing_signal_keys.add((row["ticker"], row["created_at"]))

        for st in payload.get("settlements") or []:
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
                    st["ticker"],
                    st.get("event_ticker"),
                    st.get("result"),
                    st.get("settled_at"),
                    st.get("raw_json"),
                ),
            )
            imported_settlements += 1

        for sig in payload.get("signals") or []:
            key = (sig["ticker"], sig["created_at"])
            if merge and key in existing_signal_keys:
                skipped_signals += 1
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO signals
                (run_id, created_at, city_id, ticker, event_ticker, target_date, action, side,
                 execution, model_p, market_mid, yes_bid, yes_ask, spread, fee, edge,
                 suggested_contracts, reason, meta_json, outcome, settled_at, pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sig.get("run_id"),
                    sig["created_at"],
                    sig["city_id"],
                    sig["ticker"],
                    sig.get("event_ticker"),
                    sig.get("target_date"),
                    sig["action"],
                    sig.get("side"),
                    sig.get("execution"),
                    sig["model_p"],
                    sig["market_mid"],
                    sig.get("yes_bid"),
                    sig.get("yes_ask"),
                    sig.get("spread"),
                    sig.get("fee"),
                    sig["edge"],
                    sig.get("suggested_contracts"),
                    sig.get("reason"),
                    sig.get("meta_json"),
                    sig.get("outcome"),
                    sig.get("settled_at"),
                    sig.get("pnl"),
                ),
            )
            imported_signals += 1

    ledger.apply_settlements_to_signals()
    return {
        "ok": True,
        "exported_at": payload.get("exported_at"),
        "imported_signals": imported_signals,
        "skipped_signals": skipped_signals,
        "imported_settlements": imported_settlements,
        "stats": ledger.signal_stats(),
    }


def import_if_newer(ledger: Ledger, path: Path) -> dict[str, Any]:
    """Import snapshot when file is newer than newest local signal."""
    if not path.exists():
        return {"ok": False, "skipped": True, "reason": "no snapshot file"}

    payload = json.loads(path.read_text(encoding="utf-8"))
    snap_ts = _parse_ts(payload.get("exported_at"))
    with ledger.connect() as conn:
        row = conn.execute("SELECT MAX(created_at) AS m FROM signals").fetchone()
    local_ts = _parse_ts(row["m"] if row else None)

    if snap_ts and local_ts and snap_ts <= local_ts:
        return {
            "ok": True,
            "skipped": True,
            "reason": "local ledger is current",
            "exported_at": payload.get("exported_at"),
        }
    return import_ledger_snapshot(ledger, path, merge=True)
