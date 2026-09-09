from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.feed.bars import BarsFeed, _build_path, corwin_schultz_spread, fetch_bars

NETWORK_TICKER = "SAP.DE"
NETWORK_ISIN = "DE0007164600"


def test_corwin_schultz_zero_range_gives_zero_spread() -> None:
    assert corwin_schultz_spread(100.0, 100.0, 100.0, 100.0) == 0.0


def test_corwin_schultz_wider_range_gives_larger_spread() -> None:
    tight = corwin_schultz_spread(100.5, 99.5, 100.5, 99.5)
    wide = corwin_schultz_spread(103.0, 97.0, 103.0, 97.0)
    assert wide > tight > 0.0


def test_corwin_schultz_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        corwin_schultz_spread(-1.0, 1.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        corwin_schultz_spread(1.0, 2.0, 1.0, 1.0)  # high < low


def test_corwin_schultz_clamped_to_zero_when_raw_negative() -> None:
    s = corwin_schultz_spread(100.01, 99.99, 100.02, 99.98)
    assert s >= 0.0


def test_build_path_from_synthetic_bars() -> None:
    idx = pd.date_range("2026-09-07 09:00", periods=5, freq="1min", tz="Europe/Berlin")
    df = pd.DataFrame(
        {
            "High": [100.5, 100.6, 100.4, 100.8, 100.7],
            "Low": [99.5, 99.6, 99.5, 99.6, 99.7],
            "Close": [100.0, 100.1, 99.9, 100.2, 100.1],
        },
        index=idx,
    )
    path = _build_path(df)
    assert len(path.times) == 5
    assert path.mids == [100.0, 100.1, 99.9, 100.2, 100.1]
    assert path.spread_fracs[0] == path.spread_fracs[1]  # backfilled
    assert all(s >= 0.0 for s in path.spread_fracs)


def test_fetch_bars_caches_to_parquet(tmp_path: Path) -> None:
    try:
        df1 = fetch_bars(NETWORK_TICKER, tmp_path, period="5d", interval="1m")
    except Exception as e:  # pragma: no cover - environment-dependent
        pytest.skip(f"network unavailable for yfinance: {e}")
    assert not df1.empty
    cache_files = list(tmp_path.glob("*.parquet"))
    assert len(cache_files) == 1
    df2 = fetch_bars(NETWORK_TICKER, tmp_path, period="5d", interval="1m")
    assert len(df1) == len(df2)


def test_bars_feed_bbo_on_real_data(tmp_path: Path) -> None:
    session = SessionCalendar.from_primitives(
        "Europe/Berlin", "09:00", "17:30", 0, ["mon", "tue", "wed", "thu", "fri"]
    )
    try:
        feed = BarsFeed(NETWORK_TICKER, NETWORK_ISIN, session, tmp_path)
    except Exception as e:  # pragma: no cover - environment-dependent
        pytest.skip(f"network unavailable for yfinance: {e}")

    assert feed.available_dates
    d = feed.available_dates[-1]
    ts = datetime(d.year, d.month, d.day, 12, 0, tzinfo=session.zone)
    bbo = feed.bbo(ts, NETWORK_ISIN, 1_000.0)
    assert bbo.bid > 0
    # a Corwin-Schultz estimate of exactly 0 for a calm bar is a legitimate
    # outcome (negative raw estimates are clamped to 0), not a bug -- so
    # this is <=, not a strict <.
    assert bbo.bid <= bbo.ask
    assert bbo.is_open

    with pytest.raises(ValueError):
        feed.bbo(ts, "WRONG-ISIN", 1_000.0)
