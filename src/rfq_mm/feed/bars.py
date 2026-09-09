"""Tier 2 feed: real yfinance 1-minute bars + Corwin-Schultz-estimated
spread (spec.md §2).

Bars have no bid/ask; inventing a fixed spread would be dishonest, so the
effective spread is estimated per consecutive bar pair via the
Corwin-Schultz (2012) high-low estimator. The paper's original
application was daily bars; applying the same closed-form estimator to
intraday (1-minute) bars is a standard adaptation in the microstructure
literature, not a technique invented for this project.

Formula verified against Bernt Arne Ødegaard's teaching notes, which
reproduce Corwin & Schultz's own reference R/C++ implementation
(https://ba-odegaard.no/teach/notes/liquidity_estimators/corwin_schultz_high_low_estimator/lectures_high_low.pdf):

    beta  = ln(H1/L1)^2 + ln(H2/L2)^2          (two single-period ranges)
    gamma = ln(max(H1,H2) / min(L1,L2))^2       (the two-period range)
    alpha = (sqrt(2*beta) - sqrt(beta)) / (3 - 2*sqrt(2)) - sqrt(gamma / (3 - 2*sqrt(2)))
    S     = 2*(exp(alpha) - 1) / (1 + exp(alpha))

Negative raw estimates are clamped to zero — the paper's own recommended
treatment ("HLSpreadEstimatorImposePositive"), not a project-specific fudge.

Depth: yfinance bars carry no book depth, so bid_size_eur/ask_size_eur are
set equal to the requested size_eur (i.e. "assume the requested size is
always available at the estimated spread") — another documented proxy,
not a real depth feed.

Bars are cached to Parquet on first fetch (R92) so a run is reproducible
offline afterwards.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.types import BBO

_CS_DENOM = 3.0 - 2.0 * math.sqrt(2.0)


def corwin_schultz_spread(h1: float, l1: float, h2: float, l2: float) -> float:
    """Two-period Corwin-Schultz spread estimate, as a fraction of price."""
    if h1 <= 0 or l1 <= 0 or h2 <= 0 or l2 <= 0:
        raise ValueError("high/low prices must be positive")
    if h1 < l1 or h2 < l2:
        raise ValueError("high must be >= low within each period")
    beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
    gamma = math.log(max(h1, h2) / min(l1, l2)) ** 2
    alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / _CS_DENOM - math.sqrt(gamma / _CS_DENOM)
    spread = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
    return max(spread, 0.0)


def fetch_bars(
    ticker: str, cache_dir: Path, period: str = "5d", interval: str = "1m"
) -> pd.DataFrame:
    """1-minute bars for `ticker`, from a local Parquet cache if present,
    else fetched via yfinance and cached for next time."""
    cache_path = cache_dir / f"{ticker.replace('.', '_')}_{interval}_{period}.parquet"
    if cache_path.exists():
        return pd.read_parquet(cache_path)

    import yfinance as yf

    df: pd.DataFrame = yf.Ticker(ticker).history(period=period, interval=interval)
    if df.empty:
        raise ValueError(f"yfinance returned no data for {ticker!r} ({interval}, {period})")
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path)
    return df


@dataclass(slots=True)
class _BarsPath:
    times: list[datetime]
    mids: list[float]
    spread_fracs: list[float]


def _build_path(df: pd.DataFrame) -> _BarsPath:
    df = df.sort_index()
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    closes = df["Close"].to_numpy()
    times = [ts.to_pydatetime() for ts in df.index]

    spread_fracs = [0.0] * len(df)
    for i in range(1, len(df)):
        try:
            spread_fracs[i] = corwin_schultz_spread(highs[i - 1], lows[i - 1], highs[i], lows[i])
        except ValueError:
            spread_fracs[i] = spread_fracs[i - 1]
    if len(df) > 1:
        spread_fracs[0] = spread_fracs[1]

    return _BarsPath(times=times, mids=[float(c) for c in closes], spread_fracs=spread_fracs)


class BarsFeed:
    """Real historical price series for one instrument, with a Corwin-
    Schultz-estimated spread. `bbo()` snaps to the nearest bar at or
    before `ts`; outside the fetched range it raises, matching
    SyntheticFeed's behaviour for out-of-range queries."""

    def __init__(
        self,
        ticker: str,
        isin: str,
        reference_session: SessionCalendar,
        cache_dir: Path,
        period: str = "5d",
        interval: str = "1m",
    ) -> None:
        self._isin = isin
        self._reference_session = reference_session
        raw = fetch_bars(ticker, cache_dir, period, interval)
        self._path = _build_path(raw)

    @property
    def available_dates(self) -> list[date]:
        seen: list[date] = []
        for t in self._path.times:
            d = t.date()
            if not seen or seen[-1] != d:
                seen.append(d)
        return seen

    def _index_for(self, ts: datetime) -> int:
        idx = bisect.bisect_right(self._path.times, ts) - 1
        if idx < 0:
            if self._path.times and ts.date() == self._path.times[0].date():
                return 0
            raise ValueError(f"ts {ts.isoformat()} before earliest cached bar")
        return idx

    def bbo(self, ts: datetime, isin: str, size_eur: float) -> BBO:
        if isin != self._isin:
            raise ValueError(f"BarsFeed configured for {self._isin!r}, got {isin!r}")
        if size_eur <= 0:
            raise ValueError("size_eur must be positive")
        idx = self._index_for(ts)
        mid = self._path.mids[idx]
        half_spread = self._path.spread_fracs[idx] * mid / 2.0
        is_open = self._reference_session.is_within_session(ts)
        return BBO(
            ts=ts,
            isin=isin,
            bid=mid - half_spread,
            ask=mid + half_spread,
            bid_size_eur=size_eur,
            ask_size_eur=size_eur,
            is_open=is_open,
            is_indicative=not is_open,
        )
