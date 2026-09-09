"""Property test on the §7(1) regulatory spread-clamp invariant (R61,
spec.md §1's "Hypothesis for ... the spread-clamp invariant").

On the obligated (non-exempt, non-discretionary) path, the quoted spread
must never exceed the reference-market spread for the requested size,
after trading/settlement/FX costs (engine/quoter.py::price_quote's `cap`)
-- regardless of how wide the Avellaneda-Stoikov risk spread wants to be.
"""

from datetime import UTC, datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rfq_mm.core.types import BBO, Instrument
from rfq_mm.engine.quoter import MarkoutTracker, QuoterParams, TradingCosts, price_quote
from rfq_mm.venue.config import VenueConfig

gammas = st.floats(min_value=0.0001, max_value=10.0, allow_nan=False, allow_infinity=False)
kappas = st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False)
sigmas = st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)
inventories = st.floats(
    min_value=-1_000.0, max_value=1_000.0, allow_nan=False, allow_infinity=False
)
t_fracs = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
half_spreads = st.floats(min_value=0.001, max_value=5.0, allow_nan=False, allow_infinity=False)


# venue_config/instrument are immutable value objects with no per-example
# state to reset, so reusing one instance across all generated examples
# (rather than reconstructing it per example) doesn't affect correctness.
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    gamma=gammas,
    kappa=kappas,
    sigma=sigmas,
    inventory_units=inventories,
    t_remaining_frac=t_fracs,
    half_spread=half_spreads,
)
def test_obligated_quote_never_exceeds_the_regulatory_cap(
    venue_config: VenueConfig,
    instrument: Instrument,
    gamma: float,
    kappa: float,
    sigma: float,
    inventory_units: float,
    t_remaining_frac: float,
    half_spread: float,
) -> None:
    # A fixed, safely-above-discretionary-threshold mid (spec.md §7(2):
    # below EUR 5/share is discretionary, not obligated) keeps every
    # example on the obligated path this invariant is about.
    mid = 220.0
    bbo = BBO(
        ts=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        isin=instrument.isin,
        bid=mid - half_spread,
        ask=mid + half_spread,
        bid_size_eur=20_000.0,
        ask_size_eur=20_000.0,
        is_open=True,
        is_indicative=False,
    )
    decision = price_quote(
        bbo=bbo,
        instrument=instrument,
        inventory_units=inventory_units,
        inventory_headroom_eur=1_000_000.0,
        is_exempt=False,
        guaranteed_volumes=venue_config.guaranteed_volumes_eur,
        spread_config=venue_config.spread,
        costs=TradingCosts(),
        params=QuoterParams(gamma=gamma, kappa=kappa, per_quote_cap_eur=1_000_000.0),
        markout_tracker=MarkoutTracker(),
        sigma=sigma,
        t_remaining_frac=t_remaining_frac,
    )
    assert not decision.is_voluntary_path
    assert decision.regulatory_cap is not None
    assert decision.quoted_delta <= decision.regulatory_cap + 1e-9
