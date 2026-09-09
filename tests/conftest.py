from datetime import date
from pathlib import Path

import pytest

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.types import Instrument, Segment
from rfq_mm.feed.synthetic import SyntheticFeed, SyntheticFeedParams
from rfq_mm.venue.config import VenueConfig, load_venue_config

REPO_ROOT = Path(__file__).parents[1]
TEST_ISIN = "DE0007164600"
TEST_DAY = date(2026, 9, 7)  # a Monday


@pytest.fixture
def venue_config() -> VenueConfig:
    return load_venue_config(REPO_ROOT / "venues" / "eix.yaml")


@pytest.fixture
def venue_session(venue_config: VenueConfig) -> SessionCalendar:
    s = venue_config.session
    return SessionCalendar.from_primitives(
        timezone=s.timezone,
        open_time=s.open,
        close_time=s.close,
        post_trading_minutes=s.post_trading_minutes,
        days=list(s.days),
    )


@pytest.fixture
def reference_session(venue_config: VenueConfig) -> SessionCalendar:
    rm = venue_config.reference_market
    return SessionCalendar.from_primitives(
        timezone=venue_config.session.timezone,
        open_time=rm.hours.open,
        close_time=rm.hours.close,
        post_trading_minutes=0,
        days=list(venue_config.session.days),
    )


@pytest.fixture
def instrument() -> Instrument:
    return Instrument(
        isin=TEST_ISIN, name="SAP SE", segment=Segment.DAX, currency="EUR", liquidity_band=6
    )


@pytest.fixture
def feed(venue_session: SessionCalendar, reference_session: SessionCalendar) -> SyntheticFeed:
    return SyntheticFeed(
        venue_session=venue_session,
        reference_session=reference_session,
        start_prices={TEST_ISIN: 220.0},
        params=SyntheticFeedParams(seed=42),
    )
