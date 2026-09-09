"""Regression checks against independently cross-verified RTS 11 table values."""

from rfq_mm.core.ticks import tick_size


def test_price_1_to_2_all_bands() -> None:
    expected = [0.01, 0.005, 0.002, 0.001, 0.0005, 0.0002]
    for band, tick in enumerate(expected, start=1):
        assert tick_size(1.50, band) == tick


def test_price_0_1_to_0_2_all_bands() -> None:
    expected = [0.001, 0.0005, 0.0002, 0.0001, 0.0001, 0.0001]
    for band, tick in enumerate(expected, start=1):
        assert tick_size(0.15, band) == tick


def test_price_50000_plus_all_bands() -> None:
    expected = [500.0, 200.0, 100.0, 50.0, 20.0, 10.0]
    for band, tick in enumerate(expected, start=1):
        assert tick_size(50_000.0, band) == tick
        assert tick_size(1_000_000.0, band) == tick
