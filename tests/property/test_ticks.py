"""Property tests on the RTS 11 tick-rounding invariant (spec.md M2 done-when, R168).

rounded bid <= raw bid; rounded ask >= raw ask; rounded bid/ask never cross.
"""

from hypothesis import given
from hypothesis import strategies as st

from rfq_mm.core.ticks import round_ask_up, round_bid_down, round_quote

prices = st.floats(min_value=0.01, max_value=99_999.0, allow_nan=False, allow_infinity=False)
bands = st.integers(min_value=1, max_value=6)
gaps = st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)


@given(price=prices, band=bands)
def test_round_bid_down_never_exceeds_raw(price: float, band: int) -> None:
    assert round_bid_down(price, band) <= price


@given(price=prices, band=bands)
def test_round_ask_up_never_below_raw(price: float, band: int) -> None:
    assert round_ask_up(price, band) >= price


@given(bid=prices, gap=gaps, band=bands)
def test_round_quote_never_crosses(bid: float, gap: float, band: int) -> None:
    ask = bid + gap
    rounded_bid, rounded_ask = round_quote(bid, ask, band)
    assert rounded_bid < rounded_ask
