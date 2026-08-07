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

    Imported signals are attached to a fresh restore run (or NULL run_id) so
    foreign keys succeed on empty Cloud DBs that lack the original run rows.
    """
    if not path.exists():
        return {"ok": False, "skipped": True, "reason": "snapshot missing"}

    payload = json.loads(path.read_text(encoding="utf-8"))
    imported_signals = 0
    imported_settlements = 0
    skipped_signals = 0
    errors: list[str] = []

    restore_run_id = ledger.start_run(
        notes=f"snapshot-import:{payload.get('exported_at') or utc_now()}"
    )

    try:
        with ledger.connect() as conn:
            existing_signal_keys = set()
            if merge:
                for row in conn.execute("SELECT ticker, created_at FROM signals").fetchall():
                    existing_signal_keys.add((row["ticker"], row["created_at"]))

            for st in payload.get("settlements") or []:
                try:
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
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"settlement {st.get('ticker')}: {exc}")

            for sig in payload.get("signals") or []:
                key = (sig["ticker"], sig["created_at"])
                if merge and key in existing_signal_keys:
                    skipped_signals += 1
                    continue
                try:
                    # Remap run_id to the restore run so FK constraints pass on Cloud.
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO signals
                        (run_id, created_at, city_id, ticker, event_ticker, target_date, action, side,
                         execution, model_p, market_mid, yes_bid, yes_ask, spread, fee, edge,
                         suggested_contracts, reason, meta_json, outcome, settled_at, pnl)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            restore_run_id,
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
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"signal {sig.get('ticker')}@{sig.get('created_at')}: {exc}")
        ledger.apply_settlements_to_signals()
    finally:
        ledger.finish_run(restore_run_id)

    return {
        "ok": True,
        "exported_at": payload.get("exported_at"),
        "imported_signals": imported_signals,
        "skipped_signals": skipped_signals,
        "imported_settlements": imported_settlements,
        "restore_run_id": restore_run_id,
        "errors": errors[:20],
        "stats": ledger.signal_stats(),
    }


def import_if_newer(ledger: Ledger, path: Path) -> dict[str, Any]:
    """Import snapshot when file has signals not yet in the local ledger."""
    if not path.exists():
        return {"ok": False, "skipped": True, "reason": "no snapshot file"}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "skipped": True, "reason": f"bad snapshot json: {exc}"}

    signals = payload.get("signals") or []
    with ledger.connect() as conn:
        existing = {
            (row["ticker"], row["created_at"])
            for row in conn.execute("SELECT ticker, created_at FROM signals").fetchall()
        }
        row = conn.execute("SELECT MAX(created_at) AS m FROM signals").fetchone()
    local_ts = _parse_ts(row["m"] if row else None)
    snap_ts = _parse_ts(payload.get("exported_at"))

    missing = sum(1 for s in signals if (s.get("ticker"), s.get("created_at")) not in existing)
    if missing == 0 and signals:
        return {
            "ok": True,
            "skipped": True,
            "reason": "all snapshot signals already present",
            "exported_at": payload.get("exported_at"),
        }

    if snap_ts and local_ts and snap_ts <= local_ts and missing == 0:
        return {
            "ok": True,
            "skipped": True,
            "reason": "local ledger is current",
            "exported_at": payload.get("exported_at"),
        }
    try:
        return import_ledger_snapshot(ledger, path, merge=True)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "skipped": True,
            "reason": f"import failed: {exc}",
            "exported_at": payload.get("exported_at"),
        }
