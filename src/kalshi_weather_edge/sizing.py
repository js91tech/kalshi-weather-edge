from __future__ import annotations

from typing import Any

from .fees import taker_fee_per_contract


def entry_price(*, side: str | None, yes_bid: float | None, yes_ask: float | None) -> float:
    side_u = (side or "YES").upper()
    if side_u == "YES":
        return max(0.0, min(1.0, float(yes_bid or 0.0)))
    return max(0.0, min(1.0, 1.0 - float(yes_ask if yes_ask is not None else 1.0)))


def fee_for_entry(entry: float, fee_rate: float) -> float:
    """Kalshi-style fee ≈ rate * p * (1-p) dollars per contract."""
    if fee_rate <= 0:
        return 0.0
    return taker_fee_per_contract(entry, fee_rate)


def expected_pnl_per_contract(
    *,
    side: str | None,
    yes_bid: float | None,
    yes_ask: float | None,
    assumed_win_rate: float,
    fee_rate: float = 0.0,
) -> dict[str, float]:
    """Net expected $/contract under an assumed win rate and fee rate."""
    entry = entry_price(side=side, yes_bid=yes_bid, yes_ask=yes_ask)
    fee = fee_for_entry(entry, fee_rate)
    win = (1.0 - entry) - fee
    loss = entry + fee
    wr = max(0.0, min(1.0, float(assumed_win_rate)))
    ev = wr * win - (1.0 - wr) * loss
    return {
        "entry": entry,
        "fee": fee,
        "win_if_right": win,
        "loss_if_wrong": loss,
        "assumed_win_rate": wr,
        "net_ev": ev,
    }


def size_contracts(
    *,
    bankroll_dollars: float | None,
    entry: float,
    risk_fraction: float,
    base_contracts: float,
    max_contracts: int,
    min_contracts: int = 1,
) -> int:
    """
    Bankroll-aware size: risk_fraction of bankroll / entry, else base_contracts.
    Always capped by max_contracts. Returns 0 when bankroll risk budget cannot fund 1 contract.
    """
    max_contracts = max(1, int(max_contracts))
    base = max(0, int(round(float(base_contracts or 1))))
    if bankroll_dollars is None or bankroll_dollars <= 0 or risk_fraction <= 0:
        return min(max(base, min_contracts if base > 0 else 0), max_contracts)
    if entry <= 0:
        return 0
    risk_budget = float(bankroll_dollars) * float(risk_fraction)
    sized = int(risk_budget // entry)
    if sized < min_contracts:
        return 0
    return min(sized, max_contracts)


def size_signal(
    signal: dict[str, Any],
    *,
    fee_rate: float,
    assumed_win_rate: float,
    require_positive_net_ev: bool,
    bankroll_dollars: float | None,
    risk_fraction: float,
    max_contracts: int,
    base_contracts: float | None = None,
) -> dict[str, Any]:
    """
    Attach fee-aware EV + sized contracts. May convert TRADE -> PASS when EV <= 0.
    """
    out = dict(signal)
    action = out.get("action")
    if action in (None, "PASS"):
        out["net_ev"] = None
        out["fee_assumption"] = fee_rate
        return out

    side = out.get("side")
    base = float(
        base_contracts
        if base_contracts is not None
        else (out.get("suggested_contracts") or 1.0)
    )
    ev = expected_pnl_per_contract(
        side=side,
        yes_bid=out.get("yes_bid"),
        yes_ask=out.get("yes_ask"),
        assumed_win_rate=assumed_win_rate,
        fee_rate=fee_rate,
    )
    contracts = size_contracts(
        bankroll_dollars=bankroll_dollars,
        entry=ev["entry"],
        risk_fraction=risk_fraction,
        base_contracts=base,
        max_contracts=max_contracts,
    )
    out["fee_assumption"] = fee_rate
    out["net_ev"] = round(ev["net_ev"], 4)
    out["net_ev_total"] = round(ev["net_ev"] * contracts, 4)
    out["assumed_win_rate"] = ev["assumed_win_rate"]
    out["suggested_contracts"] = float(contracts)

    meta = dict(out.get("meta") or {})
    meta["sizing"] = {
        "entry": round(ev["entry"], 4),
        "fee": round(ev["fee"], 4),
        "net_ev": out["net_ev"],
        "contracts": contracts,
        "bankroll": bankroll_dollars,
        "risk_fraction": risk_fraction,
    }
    out["meta"] = meta

    if require_positive_net_ev and ev["net_ev"] <= 0:
        out["action"] = "PASS"
        out["side"] = None
        out["execution"] = "none"
        out["suggested_contracts"] = 0.0
        out["reason"] = (
            f"Net EV ${ev['net_ev']:.4f}/contract <= 0 under "
            f"{ev['assumed_win_rate']:.0%} WR and fee_rate={fee_rate:.3f}"
        )
        meta["filtered_negative_ev"] = True
        out["meta"] = meta
    elif contracts <= 0:
        out["action"] = "PASS"
        out["side"] = None
        out["execution"] = "none"
        out["suggested_contracts"] = 0.0
        out["reason"] = "Risk budget too small for 1 contract at this entry price"
        meta["filtered_undersized"] = True
        out["meta"] = meta
    return out
