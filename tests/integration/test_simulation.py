"""End-to-end: flow generation -> quote pipeline -> acceptance -> fills ->
compliance -> P&L, via engine.simulation.run_day_simulation.

This is what spec.md M5's done-when means by "an end-to-end sim produces
fills and a P&L series", and M7's "P&L attribution reconciles to total".
"""

from datetime import date

from rfq_mm.analytics.metrics import compute_pnl_attribution
from rfq_mm.compliance.monitor import build_compliance_report
from rfq_mm.core.clock import SessionCalendar
from rfq_mm.engine.simulation import SimulationParams, run_day_simulation
from rfq_mm.feed.synthetic import SyntheticFeed, SyntheticFeedParams
from rfq_mm.instruments.universe import BY_ISIN, LIVE_SIM_ISINS, START_PRICES_EUR
from rfq_mm.risk.inventory import InventoryLimits
from rfq_mm.venue.config import load_venue_config

SIM_DATE = date(2026, 9, 7)  # a Monday


def build_sessions(venue_config):
    s = venue_config.session
    venue_session = SessionCalendar.from_primitives(
        s.timezone, s.open, s.close, s.post_trading_minutes, list(s.days)
    )
    rm = venue_config.reference_market
    reference_session = SessionCalendar.from_primitives(
        s.timezone, rm.hours.open, rm.hours.close, 0, list(s.days)
    )
    return venue_session, reference_session


def test_end_to_end_day_produces_fills_and_reconciling_pnl() -> None:
    venue_config = load_venue_config("venues/eix.yaml")
    venue_session, reference_session = build_sessions(venue_config)

    isins = LIVE_SIM_ISINS[:3]
    instruments = {isin: BY_ISIN[isin] for isin in isins}
    start_prices = {isin: START_PRICES_EUR[isin] for isin in isins}
    feed = SyntheticFeed(
        venue_session, reference_session, start_prices, SyntheticFeedParams(seed=11)
    )

    params = SimulationParams(
        seed=11,
        sim_date=SIM_DATE,
        isins=isins,
        flow_params_overrides={"base_lambda_per_hour": 60.0},
    )
    result = run_day_simulation(
        venue_config, venue_session, reference_session, feed, instruments, params
    )

    assert len(result.rfqs) > 50
    assert len(result.quotes) > 0
    assert len(result.fills) > 0
    assert result.response_tracker.presented > 0

    # every fill belongs to a quote that was actually issued this day
    quote_ids = {q.quote_id for q in result.quotes}
    for fill in result.fills:
        assert fill.quote_id in quote_ids

    total_pnl_by_isin = {}
    reconciliation_errors = []
    for isin in isins:
        isin_fills = [f for f in result.fills if f.isin == isin]
        isin_hedges = [h for h in result.hedges if h.isin == isin]
        attribution = compute_pnl_attribution(
            isin_fills, isin_hedges, feed, result.final_units[isin], result.final_mark[isin]
        )
        total_pnl_by_isin[isin] = attribution.total_pnl_eur
        reconciliation_errors.append(attribution.reconciliation_error_eur())

    assert all(err < 0.01 for err in reconciliation_errors)

    report = build_compliance_report(
        result.response_tracker,
        result.compliance_log,
        n_accept_attempts=len(result.fills) + len(result.compliance_log.withdrawal_events),
        min_response_ratio=venue_config.obligations.min_response_ratio,
    )
    assert report.presented == result.response_tracker.presented


def test_killed_isin_stops_quoting_for_rest_of_day() -> None:
    venue_config = load_venue_config("venues/eix.yaml")
    venue_session, reference_session = build_sessions(venue_config)

    isin = LIVE_SIM_ISINS[0]
    instruments = {isin: BY_ISIN[isin]}
    feed = SyntheticFeed(
        venue_session,
        reference_session,
        {isin: START_PRICES_EUR[isin]},
        SyntheticFeedParams(seed=3),
    )

    # A near-zero cap guarantees the very first fill trips the kill switch,
    # so this test doesn't depend on how much flow happens to accumulate.
    params = SimulationParams(
        seed=3,
        sim_date=SIM_DATE,
        isins=[isin],
        flow_params_overrides={"base_lambda_per_hour": 200.0},
        inventory_limits=InventoryLimits(
            max_abs_position_eur=1.0, kill_switch_abs_position_eur=0.5
        ),
    )
    result = run_day_simulation(
        venue_config, venue_session, reference_session, feed, instruments, params
    )
    assert result.fills  # at least one fill happened before the kill switch tripped
    assert isin in result.killed_isins

    first_fill_ts = result.fills[0].ts
    later_outcomes = [o for o in result.outcomes if o.rfq.ts > first_fill_ts]
    assert later_outcomes  # there were RFQs after the kill switch tripped
    assert all(o.quote is None for o in later_outcomes)  # all of them went unquoted
