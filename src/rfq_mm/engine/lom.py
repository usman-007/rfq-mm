"""§3/§9: the Limit Order Management (LOM) system — the second execution
path alongside the RFQ/market-maker system (§3: "Two execution paths, not
one book"). Clients rest limit orders here; on every reference-market
price update the book is rechecked, and a resting order that is now
marketable "auto-fires an RFQ" (§9) rather than executing directly
against a book — engine/simulation.py routes that synthetic RFQ through
the exact same process_rfq/accept_quote pipeline as any client-initiated
request, so a LOM-triggered trade inherits the §5 exemption check, §7(1)
clamp, and §8 TTL/withdrawal/firm-size logic like everything else.

Once triggered, an order is force-accepted at whatever the resulting
quote turns out to be, rather than re-checked against its own limit a
second time — the trigger condition (marketable against the reference
touch) is treated as a fire-once decision, similar to how a stop order
converts into a market order once triggered. This keeps the model
simple and is documented rather than silently assumed; the alternative
(re-validate against the post-quote price and re-rest the order if the
§7(1) clamp moved it back out of range) would add real complexity for a
difference that's usually a few basis points.

No public dataset of client resting-order arrivals exists for this venue
(the spec is silent on §9's arrival mechanics, unlike RFQ flow in §2), so
— like flow/generator.py — this is a documented, parameterised proxy: a
homogeneous Poisson arrival process per instrument, log-normal size (same
distribution family as RFQ flow), and a limit price offset from the
mid-at-submission by a random distance in bps. Orders rest away from the
touch at submission (a limit order submitted already-marketable wouldn't
be "resting" at all) so they only become marketable if/when the price
later moves toward them — which is what actually exercises the recheck
logic this module exists to provide.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from numpy.random import Generator, default_rng

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.types import BBO, Side
from rfq_mm.feed.base import ReferenceMarketFeed


class LimitOrderStatus(StrEnum):
    OPEN = "open"
    FILLED = "filled"


@dataclass(slots=True)
class LimitOrder:
    """A resting client limit order in the LOM book. Not one of the frozen
    §6-anchored dataclasses in core/types.py (Quote/RFQ/Fill/BBO/Position)
    — this is engine-internal state for the second execution path."""

    order_id: str
    isin: str
    requester_id: str
    side: Side
    limit_price: float
    size_eur: float
    ts_submitted: datetime
    status: LimitOrderStatus = LimitOrderStatus.OPEN

    def is_marketable(self, bbo: BBO) -> bool:
        if self.side is Side.BUY:
            return self.limit_price >= bbo.ask
        return self.limit_price <= bbo.bid


class LimitOrderBook:
    """Resting client limit orders, per ISIN."""

    def __init__(self) -> None:
        self._resting: dict[str, list[LimitOrder]] = {}

    def add(self, order: LimitOrder) -> None:
        self._resting.setdefault(order.isin, []).append(order)

    def resting_orders(self, isin: str) -> list[LimitOrder]:
        return list(self._resting.get(isin, []))

    def recheck(self, isin: str, bbo: BBO) -> list[LimitOrder]:
        """§9: re-checks resting orders for `isin` against the current
        reference-market price; returns every order that is now
        marketable, removing each from the resting book (a triggered
        order is fired exactly once — see module docstring)."""
        orders = self._resting.get(isin)
        if not orders:
            return []
        triggered = [o for o in orders if o.is_marketable(bbo)]
        if triggered:
            triggered_ids = {o.order_id for o in triggered}
            self._resting[isin] = [o for o in orders if o.order_id not in triggered_ids]
            for o in triggered:
                o.status = LimitOrderStatus.FILLED
        return triggered


@dataclass(frozen=True, slots=True)
class LomFlowParams:
    seed: int
    orders_per_hour: float = 6.0  # per instrument
    size_median_eur: float = 500.0
    size_sigma: float = 1.0
    min_size_eur: float = 50.0
    buy_probability: float = 0.5
    offset_median_bps: float = 40.0  # distance of the limit from mid-at-submission
    offset_sigma: float = 0.8


def _lom_flow_rng(seed: int, isin: str, d: date) -> Generator:
    digest = hashlib.sha256(f"lom|{isin}|{d.isoformat()}".encode()).digest()
    salt = int.from_bytes(digest[:8], "little")
    return default_rng([seed, salt & 0xFFFFFFFF, (salt >> 32) & 0xFFFFFFFF])


def generate_limit_orders(
    venue_session: SessionCalendar,
    d: date,
    isins: list[str],
    feed: ReferenceMarketFeed,
    params: LomFlowParams,
) -> list[LimitOrder]:
    """Homogeneous Poisson arrivals per instrument — §9 doesn't specify an
    arrival process the way §2 does for RFQ flow, so this documents the
    proxy the same way flow/generator.py does."""
    start = venue_session.session_open(d)
    end = venue_session.session_close(d)
    session_minutes = (end - start).total_seconds() / 60.0

    orders: list[LimitOrder] = []
    seq = 0
    for isin in isins:
        rng = _lom_flow_rng(params.seed, isin, d)
        t_minutes = 0.0
        while True:
            u = rng.random()
            t_minutes += (-math.log(u) / params.orders_per_hour) * 60.0
            if t_minutes >= session_minutes:
                break
            ts = start + timedelta(minutes=t_minutes)
            try:
                mid = feed.bbo(ts, isin, params.size_median_eur).mid
            except ValueError:
                continue  # outside the feed's generated window (e.g. post-close buffer edge)

            side = Side.BUY if rng.random() < params.buy_probability else Side.SELL
            offset_bps = float(
                rng.lognormal(mean=math.log(params.offset_median_bps), sigma=params.offset_sigma)
            )
            direction = -1.0 if side is Side.BUY else 1.0  # rest below mid for BUY, above for SELL
            limit_price = mid * (1.0 + direction * offset_bps / 10_000.0)
            raw_size = rng.lognormal(mean=math.log(params.size_median_eur), sigma=params.size_sigma)
            size = max(float(raw_size), params.min_size_eur)
            seq += 1
            orders.append(
                LimitOrder(
                    order_id=f"lom-{d.isoformat()}-{seq:06d}",
                    isin=isin,
                    requester_id=f"lom-req-{seq:05d}",
                    side=side,
                    limit_price=limit_price,
                    size_eur=size,
                    ts_submitted=ts,
                )
            )
    orders.sort(key=lambda o: o.ts_submitted)
    return orders


def recheck_tick_times(
    venue_session: SessionCalendar, d: date, recheck_seconds: float
) -> list[datetime]:
    """The "on every price update" cadence (§9) at a fixed, documented
    resolution rather than the feed's raw 1-second internal grid — a
    coarser but bounded-cost sampling of "the price changed, recheck the
    book", not a claim that these are the only moments the price moves."""
    start = venue_session.session_open(d)
    end = venue_session.session_close(d)
    step = timedelta(seconds=recheck_seconds)
    ticks = []
    t = start
    while t <= end:
        ticks.append(t)
        t += step
    return ticks
