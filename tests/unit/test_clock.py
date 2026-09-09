from datetime import date, datetime
from zoneinfo import ZoneInfo

from rfq_mm.core.clock import SessionCalendar

CAL = SessionCalendar.from_primitives(
    timezone="Europe/Berlin",
    open_time="08:00",
    close_time="22:00",
    post_trading_minutes=5,
    days=["mon", "tue", "wed", "thu", "fri"],
)
TZ = ZoneInfo("Europe/Berlin")


def test_trading_days() -> None:
    assert CAL.is_trading_day(date(2026, 9, 7))  # Monday
    assert not CAL.is_trading_day(date(2026, 9, 5))  # Saturday
    assert not CAL.is_trading_day(date(2026, 9, 6))  # Sunday


def test_within_session() -> None:
    monday_noon = datetime(2026, 9, 7, 12, 0, tzinfo=TZ)
    assert CAL.is_within_session(monday_noon)
    assert not CAL.is_within_post_trading(monday_noon)


def test_post_trading_window() -> None:
    just_after_close = datetime(2026, 9, 7, 22, 2, tzinfo=TZ)
    assert not CAL.is_within_session(just_after_close)
    assert CAL.is_within_post_trading(just_after_close)
    assert CAL.is_venue_open(just_after_close)


def test_after_post_trading_is_closed() -> None:
    late = datetime(2026, 9, 7, 22, 6, tzinfo=TZ)
    assert not CAL.is_venue_open(late)


def test_weekend_is_closed_even_within_hours() -> None:
    saturday = datetime(2026, 9, 12, 12, 0, tzinfo=TZ)
    assert not CAL.is_venue_open(saturday)
