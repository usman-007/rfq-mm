from rfq_mm.core.types import Instrument, Segment
from rfq_mm.venue.config import VenueConfig
from rfq_mm.venue.guarantees import guaranteed_volume_for


def test_normal_lookup_open_vs_closed(venue_config: VenueConfig, instrument: Instrument) -> None:
    gv = venue_config.guaranteed_volumes_eur
    open_result = guaranteed_volume_for(gv, instrument, reference_market_open=True, price=100.0)
    closed_result = guaranteed_volume_for(gv, instrument, reference_market_open=False, price=100.0)
    assert open_result.guaranteed_volume_eur == 100_000
    assert closed_result.guaranteed_volume_eur == 50_000
    assert not open_result.is_discretionary


def test_below_price_is_discretionary(venue_config: VenueConfig, instrument: Instrument) -> None:
    gv = venue_config.guaranteed_volumes_eur
    result = guaranteed_volume_for(gv, instrument, reference_market_open=True, price=2.0)
    assert result.is_discretionary
    assert result.guaranteed_volume_eur == 0.0


def test_leveraged_scaling_applied(venue_config: VenueConfig) -> None:
    gv = venue_config.guaranteed_volumes_eur
    leveraged = Instrument(
        isin="SYN-LEV-01",
        name="leveraged",
        segment=Segment.DAX,
        currency="EUR",
        liquidity_band=3,
        is_leveraged=True,
    )
    result = guaranteed_volume_for(gv, leveraged, reference_market_open=True, price=100.0)
    assert result.guaranteed_volume_eur == 100_000 * gv.leveraged_scaling
