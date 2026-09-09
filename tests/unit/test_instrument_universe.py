from rfq_mm.core.types import Segment
from rfq_mm.instruments.universe import ALL_INSTRUMENTS, BY_ISIN, LIVE_SIM_ISINS, START_PRICES_EUR


def test_all_segments_represented() -> None:
    segments = {i.segment for i in ALL_INSTRUMENTS}
    assert segments == set(Segment)


def test_every_instrument_has_a_start_price() -> None:
    for isin in BY_ISIN:
        assert isin in START_PRICES_EUR
        assert START_PRICES_EUR[isin] > 0


def test_live_sim_isins_are_eur_domestic() -> None:
    for isin in LIVE_SIM_ISINS:
        instrument = BY_ISIN[isin]
        assert instrument.currency == "EUR"
        assert instrument.fx_rate_to_eur is None


def test_foreign_instruments_have_fx_rate() -> None:
    foreign = [i for i in ALL_INSTRUMENTS if i.currency != "EUR"]
    assert foreign
    for instrument in foreign:
        assert instrument.fx_rate_to_eur is not None
