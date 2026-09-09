"""Session calendar: trading hours + post-trading phase (spec.md §0, R28-R29).

Config-driven (R194) — nothing about EIX's specific 08:00-22:00 CET is hardcoded
here; those values live in venues/eix.yaml and are passed in by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True, slots=True)
class SessionCalendar:
    timezone: str
    open_time: time
    close_time: time
    post_trading_minutes: int
    trading_days: frozenset[int]  # 0=Mon .. 6=Sun, per datetime.weekday()

    @classmethod
    def from_primitives(
        cls,
        timezone: str,
        open_time: str,
        close_time: str,
        post_trading_minutes: int,
        days: list[str],
    ) -> SessionCalendar:
        try:
            day_indices = frozenset(_WEEKDAY_CODES.index(d.lower()) for d in days)
        except ValueError as e:
            raise ValueError(f"unknown weekday code in {days!r}: {e}") from e
        return cls(
            timezone=timezone,
            open_time=time.fromisoformat(open_time),
            close_time=time.fromisoformat(close_time),
            post_trading_minutes=post_trading_minutes,
            trading_days=day_indices,
        )

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() in self.trading_days

    def session_open(self, d: date) -> datetime:
        return datetime.combine(d, self.open_time, tzinfo=self.zone)

    def session_close(self, d: date) -> datetime:
        return datetime.combine(d, self.close_time, tzinfo=self.zone)

    def post_trading_end(self, d: date) -> datetime:
        return self.session_close(d) + timedelta(minutes=self.post_trading_minutes)

    def is_within_session(self, ts: datetime) -> bool:
        """True during the main trading window (excludes post-trading)."""
        local = ts.astimezone(self.zone)
        d = local.date()
        return self.is_trading_day(d) and self.session_open(d) <= local < self.session_close(d)

    def is_within_post_trading(self, ts: datetime) -> bool:
        local = ts.astimezone(self.zone)
        d = local.date()
        return self.is_trading_day(d) and self.session_close(d) <= local < self.post_trading_end(d)

    def is_venue_open(self, ts: datetime) -> bool:
        """Trading session or post-trading phase — the venue accepts activity."""
        return self.is_within_session(ts) or self.is_within_post_trading(ts)

    def minutes_since_open(self, ts: datetime) -> float:
        """Minutes elapsed since today's session open; negative before open."""
        local = ts.astimezone(self.zone)
        delta = local - self.session_open(local.date())
        return delta.total_seconds() / 60.0

    def minutes_until_close(self, ts: datetime) -> float:
        local = ts.astimezone(self.zone)
        delta = self.session_close(local.date()) - local
        return delta.total_seconds() / 60.0
