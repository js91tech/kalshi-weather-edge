from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def contract_payoff(
    *,
    side: str | None,
    yes_bid: float | None,
    yes_ask: float | None,
    contracts: float = 1.0,
) -> dict[str, float]:
    """
    Estimate maker-style win/loss per signal (matches ledger settlement math).
    """
    contracts = float(contracts or 1.0)
    side = (side or "YES").upper()
    if side == "YES":
        entry = float(yes_bid or 0)
    else:
        entry = 1.0 - float(yes_ask if yes_ask is not None else 1.0)
    entry = max(0.0, min(1.0, entry))
    win = contracts * (1.0 - entry)
    loss = contracts * entry
    return {
        "entry_per_contract": entry,
        "win_if_right": win,
        "loss_if_wrong": loss,
        "contracts": contracts,
    }


def explain_signal(signal: dict[str, Any]) -> str:
    """One-sentence beginner explanation with payoff range."""
    action = signal.get("action")
    if action in (None, "PASS"):
        return signal.get("reason") or "No trade — market not extreme enough or filtered out."

    side = (signal.get("side") or "YES").upper()
    mid = float(signal.get("market_mid") or 0)
    contracts = float(signal.get("suggested_contracts") or signal.get("contracts") or 1)
    meta = signal.get("meta") or {}
    title = meta.get("subtitle") or meta.get("title") or signal.get("ticker") or "this market"

    pay = contract_payoff(
        side=side,
        yes_bid=signal.get("yes_bid"),
        yes_ask=signal.get("yes_ask"),
        contracts=contracts,
    )
    pct = mid * 100
    if side == "YES":
        lead = (
            f"The crowd prices **{title}** at about **{pct:.0f}% YES**. "
            f"We'd buy YES (bet it happens)."
        )
    else:
        lead = (
            f"The crowd prices **{title}** at about **{pct:.0f}% YES** "
            f"(so NO is the favorite). We'd buy NO (bet it does **not** happen)."
        )
    payoff = (
        f"If we're right: about **+${pay['win_if_right']:.2f}** total "
        f"({contracts:.0f} contract(s)). "
        f"If we're wrong: about **-${pay['loss_if_wrong']:.2f}**."
    )
    return f"{lead} {payoff}"


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add payoff + explanation fields for UI tables."""
    if row.get("action") in (None, "PASS"):
        return {
            **row,
            "win_if_right": None,
            "loss_if_wrong": None,
            "explain": row.get("reason") or "PASS",
        }
    pay = contract_payoff(
        side=row.get("side"),
        yes_bid=row.get("yes_bid"),
        yes_ask=row.get("yes_ask"),
        contracts=float(row.get("contracts") or row.get("suggested_contracts") or 1),
    )
    sig = {**row, "meta": {"title": row.get("title"), "subtitle": row.get("subtitle")}}
    return {
        **row,
        "win_if_right": round(pay["win_if_right"], 2),
        "loss_if_wrong": round(pay["loss_if_wrong"], 2),
        "explain": explain_signal(sig),
    }
