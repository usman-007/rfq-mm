"""Tier 1 feed: synthetic GBM mid-price + spread process (spec.md §2).

No network, always works, deterministic from a seed. This is the default
and the only feed used for CI/property tests/parameter sweeps.

Design notes (documented here since they're modelling choices the spec
leaves open, not literal spec requirements):

- A full day's 1-second price grid is pre-generated per (isin, date) on
  first request, rather than advanced call-by-call. Markouts need the mid
  price at t+1s/10s/60s/5min *after* a fill queried after the fact (see
  analytics/metrics.py), which means the feed must support random-access
  reads at arbitrary past-or-future timestamps within the day, not just
  sequential forward advancement.
- The grid spans the full venue session (so a price/is_open/is_indicative
  answer exists at any RFQ arrival time), but the U-shaped volatility
  seasonality is centered on the *reference market's* open/close, since
  that's when real price discovery happens; outside reference hours the
  seasonal factor is held flat at its boundary (edge-of-U) value.
- Spread widens with volatility by construction: half-spread scales with
  the same seasonal multiplier as sigma, so both effects (R87 "widens with
  volatility", R88 "widens around open/close") fall out of one formula.
- `size_eur` widens the returned spread proportionally once it exceeds the
  smaller of the touch-level bid/ask size — a simple, documented proxy for
  walking an L2 book we don't simulate.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
from numpy.random import Generator, default_rng

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.types import BBO


def _isin_rng(seed: int, isin: str, d: date) -> Generator:
    digest = hashlib.sha256(f"{isin}|{d.isoformat()}".encode()).digest()
    salt = int.from_bytes(digest[:8], "little")
    return default_rng([seed, salt & 0xFFFFFFFF, (salt >> 32) & 0xFFFFFFFF])


@dataclass(frozen=True, slots=True)
class SyntheticFeedParams:
    seed: int
    mu: float = 0.0  # session log-return drift
    sigma_base: float = 0.015  # baseline "daily" (= one session) stdev of log-returns
    k_season: float = 0.5  # U-shape amplitude: vol at open/close = sigma_base*(1+k_season)
    base_half_spread_bps: float = 5.0  # half-spread at baseline (midday) vol, in bps of mid
    base_depth_eur: float = 20_000.0
    depth_noise_frac: float = 0.15
    post_close_buffer_minutes: int = 10  # so 5-min markouts near close are computable


@dataclass(slots=True)
class _InstrumentDayPath:
    times: list[datetime]
    mids: np.ndarray
    bids: np.ndarray
    asks: np.ndarray
    bid_sizes: np.ndarray
    ask_sizes: np.ndarray
    is_open: np.ndarray
    is_indicative: np.ndarray
    start: datetime

    def index_for(self, ts: datetime) -> int:
        offset = int(round((ts - self.start).total_seconds()))
        if offset < 0 or offset >= len(self.times):
            raise ValueError(
                f"ts {ts.isoformat()} outside generated path "
                f"[{self.times[0].isoformat()}, {self.times[-1].isoformat()}]"
            )
        return offset


class SyntheticFeed:
    def __init__(
        self,
        venue_session: SessionCalendar,
        reference_session: SessionCalendar,
        start_prices: dict[str, float],
        params: SyntheticFeedParams,
    ) -> None:
        self._venue_session = venue_session
        self._reference_session = reference_session
        self._params = params
        self._last_close_price: dict[str, float] = dict(start_prices)
        self._paths: dict[tuple[str, date], _InstrumentDayPath] = {}

    def bbo(self, ts: datetime, isin: str, size_eur: float) -> BBO:
        if size_eur <= 0:
            raise ValueError("size_eur must be positive")
        local = ts.astimezone(self._venue_session.zone)
        d = local.date()
        path = self._path_for(isin, d)
        idx = path.index_for(local)

        touch_liquidity = min(path.bid_sizes[idx], path.ask_sizes[idx])
        size_factor = max(1.0, size_eur / touch_liquidity) if touch_liquidity > 0 else 1.0
        mid = float(path.mids[idx])
        half_spread = (float(path.asks[idx]) - float(path.bids[idx])) / 2.0 * size_factor

        return BBO(
            ts=ts,
            isin=isin,
            bid=mid - half_spread,
            ask=mid + half_spread,
            bid_size_eur=float(path.bid_sizes[idx]),
            ask_size_eur=float(path.ask_sizes[idx]),
            is_open=bool(path.is_open[idx]),
            is_indicative=bool(path.is_indicative[idx]),
        )

    def _path_for(self, isin: str, d: date) -> _InstrumentDayPath:
        key = (isin, d)
        cached = self._paths.get(key)
        if cached is not None:
            return cached
        start_price = self._last_close_price.get(isin)
        if start_price is None:
            raise ValueError(f"no start price configured for instrument {isin!r}")
        path = self._generate_path(isin, d, start_price)
        self._paths[key] = path
        self._last_close_price[isin] = float(path.mids[-1])
        return path

    def _generate_path(self, isin: str, d: date, start_price: float) -> _InstrumentDayPath:
        p = self._params
        session_start = self._venue_session.session_open(d)
        session_end = self._venue_session.session_close(d) + timedelta(
            minutes=p.post_close_buffer_minutes
        )
        n_steps = int((session_end - session_start).total_seconds()) + 1
        times = [session_start + timedelta(seconds=i) for i in range(n_steps)]

        ref_open = self._reference_session.session_open(d)
        ref_close = self._reference_session.session_close(d)
        ref_seconds = (ref_close - ref_open).total_seconds()
        seconds_since_ref_open = np.array(
            [(t - ref_open).total_seconds() for t in times], dtype=np.float64
        )
        x = np.clip(seconds_since_ref_open / ref_seconds, 0.0, 1.0)
        seasonal_mult = 1.0 + p.k_season * (2.0 * x - 1.0) ** 2
        sigma_local = p.sigma_base * seasonal_mult

        rng = _isin_rng(p.seed, isin, d)
        session_seconds = (
            self._venue_session.session_close(d) - self._venue_session.session_open(d)
        ).total_seconds()
        dt_frac = 1.0 / session_seconds
        z = rng.standard_normal(n_steps)
        increments = (p.mu - 0.5 * sigma_local**2) * dt_frac + sigma_local * np.sqrt(dt_frac) * z
        increments[0] = 0.0
        log_prices = np.log(start_price) + np.cumsum(increments)
        mids = np.exp(log_prices)

        half_spread = p.base_half_spread_bps / 10_000.0 * mids * seasonal_mult
        bids = mids - half_spread
        asks = mids + half_spread

        bid_sizes = p.base_depth_eur * np.clip(
            rng.normal(1.0, p.depth_noise_frac, n_steps), 0.2, None
        )
        ask_sizes = p.base_depth_eur * np.clip(
            rng.normal(1.0, p.depth_noise_frac, n_steps), 0.2, None
        )

        weekday_ok = np.array([self._reference_session.is_trading_day(t.date()) for t in times])
        within_hours = (seconds_since_ref_open >= 0) & (seconds_since_ref_open < ref_seconds)
        is_open = weekday_ok & within_hours
        is_indicative = ~is_open

        return _InstrumentDayPath(
            times=times,
            mids=mids,
            bids=bids,
            asks=asks,
            bid_sizes=bid_sizes,
            ask_sizes=ask_sizes,
            is_open=is_open,
            is_indicative=is_indicative,
            start=session_start,
        )
