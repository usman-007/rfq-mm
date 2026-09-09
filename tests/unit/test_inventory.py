from datetime import UTC, datetime

from rfq_mm.core.types import Fill, Side
from rfq_mm.risk.inventory import InventoryBook, InventoryLimits

TS = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
ISIN = "X"


def make_fill(side: Side, price: float, size_eur: float) -> Fill:
    return Fill(
        fill_id="f1",
        quote_id="q1",
        ts=TS,
        isin=ISIN,
        side=side,
        price=price,
        size_eur=size_eur,
        requester_id="req-1",
        is_informed_label=False,
    )


def test_first_fill_sets_position_opposite_client_side() -> None:
    book = InventoryBook()
    book.record_fill(make_fill(Side.BUY, 100.0, 1_000.0))  # client buys -> MM sells -> MM short
    assert book.units(ISIN) == -10.0
    assert book.position(ISIN).avg_price == 100.0


def test_increasing_position_uses_weighted_average() -> None:
    book = InventoryBook()
    book.record_fill(make_fill(Side.SELL, 100.0, 1_000.0))  # client sells -> MM buys -> +10 units
    book.record_fill(make_fill(Side.SELL, 110.0, 1_000.0))  # +~9.09 more units at 110
    assert book.units(ISIN) > 10.0
    pos = book.position(ISIN)
    assert 100.0 < pos.avg_price < 110.0


def test_reducing_position_keeps_avg_price() -> None:
    book = InventoryBook()
    book.record_fill(make_fill(Side.SELL, 100.0, 2_000.0))  # +20 units @ 100
    book.record_fill(make_fill(Side.BUY, 150.0, 1_500.0))  # -10 units @ 150 (partial reduce)
    pos = book.position(ISIN)
    assert pos.quantity_units == 10.0
    assert pos.avg_price == 100.0  # unchanged by the reducing trade


def test_flipping_position_resets_avg_price_to_fill_price() -> None:
    book = InventoryBook()
    book.record_fill(make_fill(Side.SELL, 100.0, 1_000.0))  # +10 units @ 100
    book.record_fill(make_fill(Side.BUY, 200.0, 3_000.0))  # -15 units @ 200 -> flips to -5
    pos = book.position(ISIN)
    assert pos.quantity_units == -5.0
    assert pos.avg_price == 200.0


def test_headroom_shrinks_with_position_and_floors_at_zero() -> None:
    limits = InventoryLimits(max_abs_position_eur=1_000.0, kill_switch_abs_position_eur=2_000.0)
    book = InventoryBook(limits=limits)
    assert book.headroom_eur(ISIN, 100.0) == 1_000.0
    book.record_fill(make_fill(Side.SELL, 100.0, 900.0))  # MM +9 units -> 900 EUR at mark 100
    assert book.headroom_eur(ISIN, 100.0) == 100.0
    book.record_fill(make_fill(Side.SELL, 100.0, 500.0))
    assert book.headroom_eur(ISIN, 100.0) == 0.0


def test_kill_switch_triggers_above_threshold() -> None:
    limits = InventoryLimits(max_abs_position_eur=1_000.0, kill_switch_abs_position_eur=1_500.0)
    book = InventoryBook(limits=limits)
    assert not book.is_killed(ISIN, 100.0)
    book.record_fill(make_fill(Side.SELL, 100.0, 1_600.0))  # +16 units -> 1600 EUR at mark 100
    assert book.is_killed(ISIN, 100.0)


def test_apply_hedge_reduces_position() -> None:
    book = InventoryBook()
    book.record_fill(make_fill(Side.SELL, 100.0, 1_000.0))  # +10 units
    book.apply_hedge(ISIN, hedge_side_sign=-1, units=4.0, price=101.0, ts=TS)  # sell 4 units
    assert book.units(ISIN) == 6.0
