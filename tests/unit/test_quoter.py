from datetime import UTC, datetime

from rfq_mm.core.types import BBO, Instrument
from rfq_mm.engine.quoter import (
    MarkoutTracker,
    QuoterParams,
    TradingCosts,
    cost_allowance_price,
    fair_value,
    price_quote,
    reservation_price,
    risk_spread_width,
)
from rfq_mm.venue.config import SpreadConfig, VenueConfig


def make_bbo(bid: float = 219.99, ask: float = 220.01, is_open: bool = True) -> BBO:
    return BBO(
        ts=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        isin="DE0007164600",
        bid=bid,
        ask=ask,
        bid_size_eur=20_000.0,
        ask_size_eur=20_000.0,
        is_open=is_open,
        is_indicative=False,
    )


def test_fair_value_mid_vs_micro_price() -> None:
    bbo = BBO(
        ts=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        isin="X",
        bid=100.0,
        ask=102.0,
        bid_size_eur=1_000.0,
        ask_size_eur=3_000.0,
        is_open=True,
        is_indicative=False,
    )
    assert fair_value(bbo, use_micro_price=False) == 101.0
    micro = fair_value(bbo, use_micro_price=True)
    assert micro == (100.0 * 3_000.0 + 102.0 * 1_000.0) / 4_000.0
    assert micro != 101.0


def test_reservation_price_skews_with_inventory() -> None:
    fv = 100.0
    r_long = reservation_price(fv, inventory_units=10.0, gamma=0.1, sigma=2.0, t_remaining_frac=1.0)
    r_flat = reservation_price(fv, inventory_units=0.0, gamma=0.1, sigma=2.0, t_remaining_frac=1.0)
    r_short = reservation_price(
        fv, inventory_units=-10.0, gamma=0.1, sigma=2.0, t_remaining_frac=1.0
    )
    assert r_long < r_flat == fv < r_short


def test_risk_spread_width_is_positive() -> None:
    delta = risk_spread_width(gamma=0.1, sigma=2.0, t_remaining_frac=1.0, kappa=1.5)
    assert delta > 0


def test_cost_allowance_respects_include_costs_and_foreign_fx() -> None:
    costs = TradingCosts(trading_cost_bps=2.0, settlement_cost_bps=1.0, fx_cost_bps=3.0)
    cfg_all = SpreadConfig(
        rule="not_outside_reference_spread",
        include_costs=["trading", "settlement", "fx"],
        depth_aware=True,
    )
    cfg_no_fx = SpreadConfig(
        rule="not_outside_reference_spread",
        include_costs=["trading", "settlement"],
        depth_aware=True,
    )
    mid = 100.0
    domestic = cost_allowance_price(cfg_all, costs, mid, is_foreign=False)
    foreign_all = cost_allowance_price(cfg_all, costs, mid, is_foreign=True)
    foreign_no_fx = cost_allowance_price(cfg_no_fx, costs, mid, is_foreign=True)
    assert domestic == (2.0 + 1.0) / 10_000.0 * mid
    assert foreign_all == (2.0 + 1.0 + 3.0) / 10_000.0 * mid
    assert foreign_no_fx == (2.0 + 1.0) / 10_000.0 * mid


def test_price_quote_clamped_when_risk_spread_exceeds_cap(
    venue_config: VenueConfig, instrument: Instrument
) -> None:
    bbo = make_bbo()
    params = QuoterParams(gamma=5.0, kappa=0.1, per_quote_cap_eur=1_000_000.0)
    decision = price_quote(
        bbo=bbo,
        instrument=instrument,
        inventory_units=0.0,
        inventory_headroom_eur=1_000_000.0,
        is_exempt=False,
        guaranteed_volumes=venue_config.guaranteed_volumes_eur,
        spread_config=venue_config.spread,
        costs=TradingCosts(),
        params=params,
        markout_tracker=MarkoutTracker(),
        sigma=5.0,
        t_remaining_frac=1.0,
    )
    assert decision.was_clamped
    assert decision.regulatory_cap is not None
    assert decision.quoted_delta == decision.regulatory_cap
    assert decision.quoted_delta < decision.risk_delta


def test_price_quote_exempt_path_is_unclamped(
    venue_config: VenueConfig, instrument: Instrument
) -> None:
    bbo = make_bbo()
    params = QuoterParams(gamma=5.0, kappa=0.1, per_quote_cap_eur=50_000.0)
    decision = price_quote(
        bbo=bbo,
        instrument=instrument,
        inventory_units=0.0,
        inventory_headroom_eur=1_000_000.0,
        is_exempt=True,
        guaranteed_volumes=venue_config.guaranteed_volumes_eur,
        spread_config=venue_config.spread,
        costs=TradingCosts(),
        params=params,
        markout_tracker=MarkoutTracker(),
        sigma=5.0,
        t_remaining_frac=1.0,
    )
    assert decision.is_voluntary_path
    assert not decision.was_clamped
    assert decision.regulatory_cap is None
    assert decision.quoted_delta == decision.risk_delta
    assert decision.firm_size_eur == params.per_quote_cap_eur


def test_price_quote_below_price_is_discretionary(
    venue_config: VenueConfig, instrument: Instrument
) -> None:
    bbo = make_bbo(bid=2.99, ask=3.01)
    decision = price_quote(
        bbo=bbo,
        instrument=instrument,
        inventory_units=0.0,
        inventory_headroom_eur=1_000_000.0,
        is_exempt=False,
        guaranteed_volumes=venue_config.guaranteed_volumes_eur,
        spread_config=venue_config.spread,
        costs=TradingCosts(),
        params=QuoterParams(),
        markout_tracker=MarkoutTracker(),
        sigma=0.05,
        t_remaining_frac=1.0,
    )
    assert decision.is_voluntary_path
    assert not decision.was_clamped


def test_adverse_selection_premium_widens_spread(
    venue_config: VenueConfig, instrument: Instrument
) -> None:
    bbo = make_bbo()
    params = QuoterParams()
    tracker_flat = MarkoutTracker()
    tracker_adverse = MarkoutTracker()
    tracker_adverse.observe(instrument.isin, 50.0)

    def price(tracker: MarkoutTracker) -> float:
        return price_quote(
            bbo=bbo,
            instrument=instrument,
            inventory_units=0.0,
            inventory_headroom_eur=1_000_000.0,
            is_exempt=True,
            guaranteed_volumes=venue_config.guaranteed_volumes_eur,
            spread_config=venue_config.spread,
            costs=TradingCosts(),
            params=params,
            markout_tracker=tracker,
            sigma=2.0,
            t_remaining_frac=1.0,
        ).risk_delta

    assert price(tracker_adverse) > price(tracker_flat)


def test_markout_tracker_ewma() -> None:
    tracker = MarkoutTracker(alpha=0.5)
    assert tracker.current("X") == 0.0
    tracker.observe("X", 10.0)
    assert tracker.current("X") == 5.0
    tracker.observe("X", 10.0)
    assert tracker.current("X") == 7.5
