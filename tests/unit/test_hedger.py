from datetime import UTC, datetime

from rfq_mm.core.types import BBO, Side
from rfq_mm.risk.hedger import HedgeParams, maybe_hedge

TS = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def make_bbo(mid: float = 100.0, spread: float = 0.1) -> BBO:
    return BBO(
        ts=TS,
        isin="X",
        bid=mid - spread / 2,
        ask=mid + spread / 2,
        bid_size_eur=10_000.0,
        ask_size_eur=10_000.0,
        is_open=True,
        is_indicative=False,
    )


def test_no_hedge_below_trigger() -> None:
    params = HedgeParams(trigger_abs_position_eur=150_000.0, target_abs_position_eur=50_000.0)
    result = maybe_hedge("X", position_units=1_000.0, mark_bbo=make_bbo(), ts=TS, params=params)
    assert result is None  # 1000 units * 100 = 100,000 EUR, below the 150k trigger


def test_long_position_hedges_by_selling() -> None:
    params = HedgeParams(trigger_abs_position_eur=150_000.0, target_abs_position_eur=50_000.0)
    result = maybe_hedge("X", position_units=2_000.0, mark_bbo=make_bbo(), ts=TS, params=params)
    assert result is not None
    assert result.side == Side.SELL
    assert result.units == 1_500.0  # reduce from 2000 to target 500 units (50k/100)
    assert result.cost_eur > 0


def test_short_position_hedges_by_buying() -> None:
    params = HedgeParams(trigger_abs_position_eur=150_000.0, target_abs_position_eur=50_000.0)
    result = maybe_hedge("X", position_units=-2_000.0, mark_bbo=make_bbo(), ts=TS, params=params)
    assert result is not None
    assert result.side == Side.BUY
    assert result.units == 1_500.0
    assert result.cost_eur > 0


def test_slippage_scales_cost() -> None:
    params_low = HedgeParams(
        slippage_bps=1.0, trigger_abs_position_eur=0.0, target_abs_position_eur=0.0
    )
    params_high = HedgeParams(
        slippage_bps=50.0, trigger_abs_position_eur=0.0, target_abs_position_eur=0.0
    )
    r_low = maybe_hedge("X", 1_000.0, make_bbo(), TS, params_low)
    r_high = maybe_hedge("X", 1_000.0, make_bbo(), TS, params_high)
    assert r_low is not None and r_high is not None
    assert r_high.cost_eur > r_low.cost_eur
