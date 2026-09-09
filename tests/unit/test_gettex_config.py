from pathlib import Path

from rfq_mm.core.types import Segment
from rfq_mm.venue.config import load_venue_config

GETTEX_YAML = Path(__file__).parents[2] / "venues" / "gettex.yaml"


def test_gettex_yaml_loads() -> None:
    config = load_venue_config(GETTEX_YAML)
    assert config.venue.name == "gettex (Börse München)"
    assert config.quote.acceptance_window_seconds == 120
    assert len(config.obligations.exemptions) == 4


def test_gettex_yaml_has_all_segments() -> None:
    config = load_venue_config(GETTEX_YAML)
    for segment in Segment:
        assert segment in config.guaranteed_volumes_eur.bands


def test_gettex_differs_from_eix_on_key_figures() -> None:
    from rfq_mm.venue.config import load_venue_config as load

    eix = load(Path(__file__).parents[2] / "venues" / "eix.yaml")
    gettex = load(GETTEX_YAML)
    assert gettex.quote.acceptance_window_seconds != eix.quote.acceptance_window_seconds
    assert (
        gettex.guaranteed_volumes_eur.volume_for(Segment.DAX, reference_market_open=True)
        != eix.guaranteed_volumes_eur.volume_for(Segment.DAX, reference_market_open=True)
    )
