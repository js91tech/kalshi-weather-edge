from kalshi_weather_edge.sizing import expected_pnl_per_contract, size_contracts, size_signal


def test_expected_pnl_maker_favorite_yes():
    ev = expected_pnl_per_contract(
        side="YES",
        yes_bid=0.90,
        yes_ask=0.92,
        assumed_win_rate=0.95,
        fee_rate=0.0,
    )
    assert abs(ev["entry"] - 0.90) < 1e-9
    assert abs(ev["win_if_right"] - 0.10) < 1e-9
    assert abs(ev["loss_if_wrong"] - 0.90) < 1e-9
    # 0.95*0.10 - 0.05*0.90 = 0.095 - 0.045 = 0.05
    assert abs(ev["net_ev"] - 0.05) < 1e-9


def test_taker_fee_reduces_ev():
    maker = expected_pnl_per_contract(
        side="YES", yes_bid=0.90, yes_ask=0.92, assumed_win_rate=0.94, fee_rate=0.0
    )
    taker = expected_pnl_per_contract(
        side="YES", yes_bid=0.90, yes_ask=0.92, assumed_win_rate=0.94, fee_rate=0.07
    )
    assert taker["fee"] > 0
    assert taker["net_ev"] < maker["net_ev"]


def test_bankroll_sizing():
    # $100 bankroll, 2% risk, entry 0.90 -> risk $2 / 0.90 => 2 contracts
    assert size_contracts(
        bankroll_dollars=100,
        entry=0.90,
        risk_fraction=0.02,
        base_contracts=1,
        max_contracts=25,
    ) == 2


def test_size_signal_filters_negative_ev():
    sig = {
        "action": "BUY_YES",
        "side": "YES",
        "yes_bid": 0.90,
        "yes_ask": 0.92,
        "suggested_contracts": 1,
        "execution": "maker",
        "meta": {},
    }
    out = size_signal(
        sig,
        fee_rate=0.07,
        assumed_win_rate=0.90,  # break-even-ish before fees; with fees should be <= 0
        require_positive_net_ev=True,
        bankroll_dollars=None,
        risk_fraction=0.02,
        max_contracts=5,
    )
    assert out["action"] == "PASS"
    assert out["meta"].get("filtered_negative_ev") is True
