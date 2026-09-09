from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.feed.synthetic import SyntheticFeed, SyntheticFeedParams

TZ = ZoneInfo("Europe/Berlin")
VENUE_SESSION = SessionCalendar.from_primitives(
    timezone="Europe/Berlin",
    open_time="08:00",
    close_time="22:00",
    post_trading_minutes=5,
    days=["mon", "tue", "wed", "thu", "fri"],
)
REFERENCE_SESSION = SessionCalendar.from_primitives(
    timezone="Europe/Berlin",
    open_time="09:00",
    close_time="17:30",
    post_trading_minutes=0,
    days=["mon", "tue", "wed", "thu", "fri"],
)
ISIN = "DE0007164600"  # SAP, used as a proxy ticker for the test fixture
DAY = date(2026, 9, 7)  # a Monday


def make_feed(seed: int = 42) -> SyntheticFeed:
    return SyntheticFeed(
        venue_session=VENUE_SESSION,
        reference_session=REFERENCE_SESSION,
        start_prices={ISIN: 100.0},
        params=SyntheticFeedParams(seed=seed),
    )


def test_full_day_streams_deterministically_from_seed() -> None:
    feed_a = make_feed(seed=42)
    feed_b = make_feed(seed=42)

    start = datetime(2026, 9, 7, 8, 0, tzinfo=TZ)
    end = datetime(2026, 9, 7, 22, 0, tzinfo=TZ)
    ts = start
    seen = 0
    while ts < end:
        bbo_a = feed_a.bbo(ts, ISIN, 1_000.0)
        bbo_b = feed_b.bbo(ts, ISIN, 1_000.0)
        assert bbo_a.bid == bbo_b.bid
        assert bbo_a.ask == bbo_b.ask
        assert bbo_a.bid < bbo_a.ask
        ts += timedelta(minutes=17)
        seen += 1
    assert seen > 40


def test_different_seeds_diverge() -> None:
    feed_a = make_feed(seed=1)
    feed_b = make_feed(seed=2)
    ts = datetime(2026, 9, 7, 12, 0, tzinfo=TZ)
    assert feed_a.bbo(ts, ISIN, 1_000.0).mid != feed_b.bbo(ts, ISIN, 1_000.0).mid


def test_is_open_tracks_reference_hours() -> None:
    feed = make_feed()
    before_open = datetime(2026, 9, 7, 8, 30, tzinfo=TZ)
    during = datetime(2026, 9, 7, 12, 0, tzinfo=TZ)
    after_close = datetime(2026, 9, 7, 18, 0, tzinfo=TZ)

    assert feed.bbo(before_open, ISIN, 1_000.0).is_open is False
    assert feed.bbo(before_open, ISIN, 1_000.0).is_indicative is True
    assert feed.bbo(during, ISIN, 1_000.0).is_open is True
    assert feed.bbo(during, ISIN, 1_000.0).is_indicative is False
    assert feed.bbo(after_close, ISIN, 1_000.0).is_open is False


def test_larger_size_widens_spread() -> None:
    feed = make_feed()
    ts = datetime(2026, 9, 7, 12, 0, tzinfo=TZ)
    small = feed.bbo(ts, ISIN, 100.0)
    huge = feed.bbo(ts, ISIN, 10_000_000.0)
    assert (huge.ask - huge.bid) > (small.ask - small.bid)


def test_spread_narrower_at_midday_than_at_open() -> None:
    feed = make_feed()
    at_open = feed.bbo(datetime(2026, 9, 7, 9, 0, tzinfo=TZ), ISIN, 1_000.0)
    at_midday = feed.bbo(datetime(2026, 9, 7, 13, 0, tzinfo=TZ), ISIN, 1_000.0)
    assert at_midday.spread < at_open.spread
