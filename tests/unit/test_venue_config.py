from pathlib import Path

import pydantic
import pytest
import yaml

from rfq_mm.core.types import Segment
from rfq_mm.venue.config import load_venue_config

EIX_YAML = Path(__file__).parents[2] / "venues" / "eix.yaml"


def test_eix_yaml_loads() -> None:
    config = load_venue_config(EIX_YAML)
    assert config.venue.name == "EIX (European Investor Exchange)"
    assert config.obligations.min_response_ratio == 0.95
    assert config.quote.acceptance_window_seconds == 10
    assert config.quote.withdrawal_window_seconds == 25
    assert len(config.obligations.exemptions) == 9


def test_eix_yaml_has_all_segments() -> None:
    config = load_venue_config(EIX_YAML)
    for segment in Segment:
        assert segment in config.guaranteed_volumes_eur.bands


def test_eix_yaml_guaranteed_volume_lookup() -> None:
    config = load_venue_config(EIX_YAML)
    gv = config.guaranteed_volumes_eur
    assert gv.volume_for(Segment.DAX, reference_market_open=True) == 100_000
    assert gv.volume_for(Segment.DAX, reference_market_open=False) == 50_000
    assert gv.volume_for(Segment.ETF_DAX, reference_market_open=True) == 500_000


def test_unknown_top_level_key_fails_loudly() -> None:
    raw = yaml.safe_load(EIX_YAML.read_text())
    raw["not_a_real_field"] = 123
    with pytest.raises(pydantic.ValidationError):
        load_venue_config_from_dict(raw)


def test_missing_segment_fails_loudly() -> None:
    raw = yaml.safe_load(EIX_YAML.read_text())
    del raw["guaranteed_volumes_eur"]["DAX"]
    with pytest.raises(pydantic.ValidationError):
        load_venue_config_from_dict(raw)


def test_bad_exemption_params_fail_loudly() -> None:
    raw = yaml.safe_load(EIX_YAML.read_text())
    raw["obligations"]["exemptions"][0] = {"type": "after_venue_open", "wrong_field": 1}
    with pytest.raises(pydantic.ValidationError):
        load_venue_config_from_dict(raw)


def test_response_ratio_out_of_range_fails_loudly() -> None:
    raw = yaml.safe_load(EIX_YAML.read_text())
    raw["obligations"]["min_response_ratio"] = 1.5
    with pytest.raises(pydantic.ValidationError):
        load_venue_config_from_dict(raw)


def load_venue_config_from_dict(raw: dict) -> None:
    from rfq_mm.venue.config import VenueConfig

    VenueConfig.model_validate(raw)
