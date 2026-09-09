"""MiFID II RTS 11 tick-size regime + rounding (spec.md §0, R135-R138).

Table source: Commission Delegated Regulation (EU) 2017/588 of 14.7.2016
("RTS 11"), Annex. The Commission's own PDF of the adopted act (C(2016) 4389
final) does not include the Annex table text in extractable form, so the
19-row x 6-band table below was cross-checked against three independent
reproductions that agree on every value spot-checked:
  - https://www.emissions-euets.com/tick-size-regime (full table)
  - https://www.wienerborse.at/en/trading/trading-information/tick-size/
    (band boundaries + 3 sample rows, all matching)
  - Deutsche Börse Xetra Circular 024/19 (confirms Art. 2(3)-(4): ETFs whose
    underlying is exclusively in-scope shares are assigned liquidity band 6)

Liquidity bands are by average daily number of transactions (ADNT) in the
most relevant market. We have no transaction-history feed to compute a real
ADNT, so `Instrument.liquidity_band` is manually assigned in the curated
instrument fixture (documented there as another proxy) rather than derived —
except for ETFs, which the regulation fixes to band 6 regardless of ADNT.

Band index -> ADNT range (least liquid to most liquid):
  1: 0-10   2: 10-80   3: 80-600   4: 600-2,000   5: 2,000-9,000   6: 9,000+
"""

from __future__ import annotations

import math
from bisect import bisect_right

# (upper price bound exclusive, tick sizes for bands 1..6, narrowest last)
# The final row's upper bound is +inf (50,000 and above).
_TABLE: tuple[tuple[float, tuple[float, float, float, float, float, float]], ...] = (
    (0.1, (0.0005, 0.0002, 0.0001, 0.0001, 0.0001, 0.0001)),
    (0.2, (0.001, 0.0005, 0.0002, 0.0001, 0.0001, 0.0001)),
    (0.5, (0.002, 0.001, 0.0005, 0.0002, 0.0001, 0.0001)),
    (1.0, (0.005, 0.002, 0.001, 0.0005, 0.0002, 0.0001)),
    (2.0, (0.01, 0.005, 0.002, 0.001, 0.0005, 0.0002)),
    (5.0, (0.02, 0.01, 0.005, 0.002, 0.001, 0.0005)),
    (10.0, (0.05, 0.02, 0.01, 0.005, 0.002, 0.001)),
    (20.0, (0.1, 0.05, 0.02, 0.01, 0.005, 0.002)),
    (50.0, (0.2, 0.1, 0.05, 0.02, 0.01, 0.005)),
    (100.0, (0.5, 0.2, 0.1, 0.05, 0.02, 0.01)),
    (200.0, (1.0, 0.5, 0.2, 0.1, 0.05, 0.02)),
    (500.0, (2.0, 1.0, 0.5, 0.2, 0.1, 0.05)),
    (1_000.0, (5.0, 2.0, 1.0, 0.5, 0.2, 0.1)),
    (2_000.0, (10.0, 5.0, 2.0, 1.0, 0.5, 0.2)),
    (5_000.0, (20.0, 10.0, 5.0, 2.0, 1.0, 0.5)),
    (10_000.0, (50.0, 20.0, 10.0, 5.0, 2.0, 1.0)),
    (20_000.0, (100.0, 50.0, 20.0, 10.0, 5.0, 2.0)),
    (50_000.0, (200.0, 100.0, 50.0, 20.0, 10.0, 5.0)),
    (math.inf, (500.0, 200.0, 100.0, 50.0, 20.0, 10.0)),
)

_UPPER_BOUNDS = [row[0] for row in _TABLE]

ETF_LIQUIDITY_BAND = 6  # RTS 11 Art. 2(3)-(4)


def tick_size(price: float, liquidity_band: int) -> float:
    """Minimum tick size for `price` in RTS 11 `liquidity_band` (1-6)."""
    if price <= 0:
        raise ValueError("price must be positive")
    if not 1 <= liquidity_band <= 6:
        raise ValueError(f"liquidity_band must be 1-6, got {liquidity_band}")
    row_idx = bisect_right(_UPPER_BOUNDS, price)
    row_idx = min(row_idx, len(_TABLE) - 1)
    return _TABLE[row_idx][1][liquidity_band - 1]


def round_bid_down(price: float, liquidity_band: int) -> float:
    """Round a bid down to the nearest valid tick (never above the raw price).

    Uses a bare floor of price/tick rather than pre-smoothing the ratio: a
    smoothing step here can round a genuinely-above-boundary float down to
    look exactly-on-boundary, which then produces a result that no longer
    satisfies the <= invariant this function exists to guarantee.
    """
    tick = tick_size(price, liquidity_band)
    ticks = math.floor(price / tick)
    return round(ticks * tick, 10)


def round_ask_up(price: float, liquidity_band: int) -> float:
    """Round an ask up to the nearest valid tick (never below the raw price)."""
    tick = tick_size(price, liquidity_band)
    ticks = math.ceil(price / tick)
    return round(ticks * tick, 10)


def round_quote(raw_bid: float, raw_ask: float, liquidity_band: int) -> tuple[float, float]:
    """Round a bid/ask pair per R136-R138: bid down, ask up, never crossed.

    If tick rounding would leave bid == ask (a raw spread narrower than one
    tick), the ask is widened by one further tick so the two never cross.
    """
    bid = round_bid_down(raw_bid, liquidity_band)
    ask = round_ask_up(raw_ask, liquidity_band)
    if bid >= ask:
        ask = round(bid + tick_size(ask, liquidity_band), 10)
    return bid, ask
