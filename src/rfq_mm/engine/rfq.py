"""§8: issue a quote with a 10s TTL, evaluate §8(2) withdrawal on
acceptance, enforce the §8(3) firm-size budget. Also runs steps 1-2 of the
spec.md §5 pipeline (exemption check, pricing) by calling into
venue/obligations.py and engine/quoter.py.

Quote:Fill is 1:many (PLAN.md Decision D8): LiveQuote.remaining_firm_size_eur
is a mutable budget decremented per accepted fill, so a quote whose firm
size is exceeded by cumulative acceptances produces a breaching fill and
blocks all further fills against it — see accept_quote().
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta

from numpy.random import Generator

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.ticks import round_quote
from rfq_mm.core.types import RFQ, Fill, Instrument, Quote, QuoteStatus, Side
from rfq_mm.engine.quoter import (
    MarkoutTracker,
    QuoterParams,
    TradingCosts,
    estimate_sigma,
    price_quote,
)
from rfq_mm.feed.base import ReferenceMarketFeed
from rfq_mm.venue.config import VenueConfig
from rfq_mm.venue.obligations import ExemptionContext, ResponseRatioTracker, evaluate_exemption

_quote_seq = itertools.count(1)
_fill_seq = itertools.count(1)


@dataclass(slots=True)
class LiveQuote:
    quote: Quote
    remaining_firm_size_eur: float
    status: QuoteStatus
    issuance_mid: float  # reference mid at issuance, for the §8(2) "market moved" check


@dataclass(frozen=True, slots=True)
class ClampEvent:
    """R133/R134: an obligated quote's risk spread exceeded the §7(1) cap."""

    ts: datetime
    isin: str
    risk_delta: float
    regulatory_cap: float


@dataclass(frozen=True, slots=True)
class WithdrawalEvent:
    """R13: §8(2) — the accepted side moved outside the current reference
    spread before the trade could be confirmed."""

    ts: datetime
    isin: str
    quote_id: str


@dataclass(frozen=True, slots=True)
class FirmSizeBreachEvent:
    """R14-R16: §8(3) — cumulative fills against one quote exceeded its
    firm size; triggers a halt of up to firm_size_breach_halt_seconds."""

    ts: datetime
    isin: str
    quote_id: str
    halt_until: datetime


@dataclass(slots=True)
class ComplianceLog:
    clamp_events: list[ClampEvent]
    withdrawal_events: list[WithdrawalEvent]
    breach_events: list[FirmSizeBreachEvent]

    @classmethod
    def empty(cls) -> ComplianceLog:
        return cls([], [], [])


@dataclass(frozen=True, slots=True)
class RfqOutcome:
    rfq: RFQ
    quote: Quote | None
    live_quote: LiveQuote | None
    reference_spread: float | None = None  # R152: bbo.spread at issuance, for charting
    regulatory_cap: float | None = None  # R153: the §7(1) cap, None on the voluntary path


def process_rfq(
    rfq: RFQ,
    *,
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: ReferenceMarketFeed,
    instrument: Instrument,
    inventory_units: float,
    inventory_headroom_eur: float,
    quoter_params: QuoterParams,
    costs: TradingCosts,
    markout_tracker: MarkoutTracker,
    response_tracker: ResponseRatioTracker,
    compliance_log: ComplianceLog,
    outage_windows: tuple[tuple[datetime, datetime], ...] = (),
    flagged_requesters: frozenset[str] = frozenset(),
) -> RfqOutcome:
    """spec.md §5 steps 1-8 (issuance only; acceptance handled separately
    by accept_quote, since it depends on client behaviour)."""
    ctx = ExemptionContext(
        venue_session=venue_session,
        reference_session=reference_session,
        feed=feed,
        outage_windows=outage_windows,
        flagged_manipulation_requesters=flagged_requesters,
    )
    evaluated_rfq = evaluate_exemption(rfq, venue_config.obligations, ctx)

    bbo = feed.bbo(evaluated_rfq.ts, evaluated_rfq.isin, evaluated_rfq.requested_size_eur)

    day = evaluated_rfq.ts.astimezone(venue_session.zone).date()
    session_seconds = (
        venue_session.session_close(day) - venue_session.session_open(day)
    ).total_seconds()
    sigma = estimate_sigma(
        feed,
        evaluated_rfq.isin,
        evaluated_rfq.ts,
        session_seconds,
        lookback_seconds=quoter_params.sigma_lookback_seconds,
        n_samples=quoter_params.sigma_samples,
    )
    t_remaining_frac = max(
        0.0,
        min(1.0, venue_session.minutes_until_close(evaluated_rfq.ts) / (session_seconds / 60.0)),
    )

    decision = price_quote(
        bbo=bbo,
        instrument=instrument,
        inventory_units=inventory_units,
        inventory_headroom_eur=inventory_headroom_eur,
        is_exempt=evaluated_rfq.is_exempt,
        guaranteed_volumes=venue_config.guaranteed_volumes_eur,
        spread_config=venue_config.spread,
        costs=costs,
        params=quoter_params,
        markout_tracker=markout_tracker,
        sigma=sigma,
        t_remaining_frac=t_remaining_frac,
    )

    if decision.was_clamped and decision.regulatory_cap is not None:
        compliance_log.clamp_events.append(
            ClampEvent(
                ts=evaluated_rfq.ts,
                isin=evaluated_rfq.isin,
                risk_delta=decision.risk_delta,
                regulatory_cap=decision.regulatory_cap,
            )
        )

    if not decision.can_quote:
        response_tracker.record(
            evaluated_rfq.isin, is_exempt=evaluated_rfq.is_exempt, was_answered=False
        )
        return RfqOutcome(
            rfq=evaluated_rfq,
            quote=None,
            live_quote=None,
            reference_spread=bbo.spread,
            regulatory_cap=decision.regulatory_cap,
        )

    bid, ask = round_quote(decision.raw_bid, decision.raw_ask, instrument.liquidity_band)
    ttl = evaluated_rfq.ts + timedelta(seconds=venue_config.quote.acceptance_window_seconds)
    quote = Quote(
        quote_id=f"q-{next(_quote_seq):08d}",
        rfq_id=evaluated_rfq.rfq_id,
        ts_issued=evaluated_rfq.ts,
        isin=evaluated_rfq.isin,
        bid=bid,
        ask=ask,
        firm_size_eur=decision.firm_size_eur,
        ttl_expiry_ts=ttl,
        is_voluntary=decision.is_voluntary_path,
    )
    live_quote = LiveQuote(
        quote=quote,
        remaining_firm_size_eur=quote.firm_size_eur,
        status=QuoteStatus.OPEN,
        issuance_mid=bbo.mid,
    )
    response_tracker.record(
        evaluated_rfq.isin, is_exempt=evaluated_rfq.is_exempt, was_answered=True
    )
    return RfqOutcome(
        rfq=evaluated_rfq,
        quote=quote,
        live_quote=live_quote,
        reference_spread=bbo.spread,
        regulatory_cap=decision.regulatory_cap,
    )


def decide_acceptance(
    quote: Quote,
    rng: Generator,
    *,
    p_accept: float = 0.9,
    reaction_delay_range: tuple[float, float] = (0.5, 8.0),
) -> datetime | None:
    """A simple, documented client-behaviour proxy: with probability
    p_accept the client accepts after a random reaction delay (clipped to
    stay within the quote's TTL); otherwise the quote lapses unaccepted."""
    if rng.random() >= p_accept:
        return None
    max_delay = (quote.ttl_expiry_ts - quote.ts_issued).total_seconds()
    delay = min(rng.uniform(*reaction_delay_range), max_delay)
    return quote.ts_issued + timedelta(seconds=delay)


def accept_quote(
    live_quote: LiveQuote,
    accept_ts: datetime,
    accept_size_eur: float,
    side: Side,
    requester_id: str,
    *,
    feed: ReferenceMarketFeed,
    venue_config: VenueConfig,
    is_informed: bool,
    informed_drift_bps: float,
    markout_tracker: MarkoutTracker,
    compliance_log: ComplianceLog,
    markout_feedback_horizon_seconds: float = 10.0,
) -> Fill | None:
    """§8(1) TTL check, §8(2) withdrawal check, §8(3) firm-size budget —
    in that order. Returns None (no trade) if any of them block the fill."""
    quote = live_quote.quote

    if live_quote.status != QuoteStatus.OPEN:
        return None
    if accept_ts > quote.ttl_expiry_ts:
        live_quote.status = QuoteStatus.EXPIRED
        return None

    trade_price = quote.ask if side is Side.BUY else quote.bid
    current_bbo = feed.bbo(accept_ts, quote.isin, accept_size_eur)
    # "the accepted side now lying outside the current reference spread" is
    # about the *reference market* moving since issuance, not about how far
    # the MM's own price sits from mid — inventory skew and the §7(1) cost
    # allowance both legitimately place trade_price away from mid even with
    # zero market movement, so comparing trade_price directly against a
    # mid-centered band (an earlier version of this check) flagged most
    # quotes as "withdrawn" regardless of whether the market had moved at
    # all. Instead: has the reference mid itself moved by more than roughly
    # one reference-spread's width since the quote was issued?
    reference_move = abs(current_bbo.mid - live_quote.issuance_mid)
    if reference_move > current_bbo.spread:
        live_quote.status = QuoteStatus.WITHDRAWN
        compliance_log.withdrawal_events.append(
            WithdrawalEvent(ts=accept_ts, isin=quote.isin, quote_id=quote.quote_id)
        )
        return None

    if accept_size_eur > live_quote.remaining_firm_size_eur:
        live_quote.status = QuoteStatus.WITHDRAWN
        halt_until = accept_ts + timedelta(seconds=venue_config.quote.firm_size_breach_halt_seconds)
        compliance_log.breach_events.append(
            FirmSizeBreachEvent(
                ts=accept_ts, isin=quote.isin, quote_id=quote.quote_id, halt_until=halt_until
            )
        )
        return None

    live_quote.remaining_firm_size_eur -= accept_size_eur
    if live_quote.remaining_firm_size_eur <= 1e-9:
        live_quote.status = QuoteStatus.CONSUMED

    fill = Fill(
        fill_id=f"f-{next(_fill_seq):08d}",
        quote_id=quote.quote_id,
        ts=accept_ts,
        isin=quote.isin,
        side=side,
        price=trade_price,
        size_eur=accept_size_eur,
        requester_id=requester_id,
        is_informed_label=is_informed,
    )

    apply_drift = getattr(feed, "apply_informed_drift", None)
    if is_informed and apply_drift is not None:
        apply_drift(quote.isin, accept_ts, side.sign * informed_drift_bps)

    try:
        future_bbo = feed.bbo(
            accept_ts + timedelta(seconds=markout_feedback_horizon_seconds),
            quote.isin,
            accept_size_eur,
        )
        markout_bps = side.sign * (future_bbo.mid - fill.price) / current_bbo.mid * 10_000.0
        markout_tracker.observe(quote.isin, markout_bps)
    except ValueError:
        pass  # too close to session end to look ahead; skip the feedback update

    return fill
