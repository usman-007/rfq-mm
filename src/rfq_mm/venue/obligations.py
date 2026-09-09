"""§5 exemption evaluation + response-ratio accounting.

Of the 9 exemption types in the rulebook (venue/config.py), this module
resolves 6 against data the simulator actually has, and documents the
other 3 as structurally present but never triggered — we have no
reference-market halt feed, no corporate-action calendar, and no discrete
"conformity check" event to hang a trigger on. Pretending to model those
would violate the project's honesty rule; leaving them permanently False
(with a comment saying why) does not.

Live (computed from real inputs):
  after_venue_open, around_reference_open  - pure calendar arithmetic
  reference_indicative_only, extreme_move  - read from the reference feed
  mm_system_outage, suspected_manipulation - triggered by caller-supplied
    context (an injected outage window / surveillance's flagged list),
    empty by default so they simply never fire unless a scenario wires
    them up (S6 stress-test config; §12 surveillance output)

Never triggered in this proxy (documented, not silently faked):
  reference_halt, corporate_action_cum_day, conformity_check
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.types import RFQ
from rfq_mm.feed.base import ReferenceMarketFeed
from rfq_mm.venue.config import (
    AfterVenueOpenExemption,
    AroundReferenceOpenExemption,
    ConformityCheckExemption,
    CorporateActionCumDayExemption,
    ExemptionConfig,
    ExtremeMoveExemption,
    MmSystemOutageExemption,
    ObligationsConfig,
    ReferenceHaltExemption,
    ReferenceIndicativeOnlyExemption,
    SuspectedManipulationExemption,
)


@dataclass(frozen=True, slots=True)
class ExemptionContext:
    venue_session: SessionCalendar
    reference_session: SessionCalendar
    feed: ReferenceMarketFeed
    outage_windows: tuple[tuple[datetime, datetime], ...] = ()
    flagged_manipulation_requesters: frozenset[str] = frozenset()


def _matches(exemption: ExemptionConfig, rfq: RFQ, ctx: ExemptionContext) -> bool:
    if isinstance(exemption, AfterVenueOpenExemption):
        minutes = ctx.venue_session.minutes_since_open(rfq.ts)
        return 0 <= minutes < exemption.minutes

    if isinstance(exemption, AroundReferenceOpenExemption):
        local_date = rfq.ts.astimezone(ctx.venue_session.zone).date()
        ref_open = ctx.reference_session.session_open(local_date)
        delta_minutes = (rfq.ts - ref_open).total_seconds() / 60.0
        return -exemption.before_minutes <= delta_minutes < exemption.after_minutes

    if isinstance(exemption, ReferenceHaltExemption):
        return False  # no halt-state feed in this proxy

    if isinstance(exemption, ReferenceIndicativeOnlyExemption):
        bbo = ctx.feed.bbo(rfq.ts, rfq.isin, rfq.requested_size_eur)
        return bbo.is_indicative

    if isinstance(exemption, CorporateActionCumDayExemption):
        return False  # no corporate-action calendar in this proxy

    if isinstance(exemption, MmSystemOutageExemption):
        return any(start <= rfq.ts < end for start, end in ctx.outage_windows)

    if isinstance(exemption, SuspectedManipulationExemption):
        return rfq.requester_id in ctx.flagged_manipulation_requesters

    if isinstance(exemption, ConformityCheckExemption):
        return False  # no discrete triggering event modelled in this proxy

    if isinstance(exemption, ExtremeMoveExemption):
        try:
            now_bbo = ctx.feed.bbo(rfq.ts, rfq.isin, rfq.requested_size_eur)
            past_bbo = ctx.feed.bbo(
                rfq.ts - timedelta(seconds=exemption.window_seconds),
                rfq.isin,
                rfq.requested_size_eur,
            )
        except ValueError:
            return False
        if past_bbo.mid <= 0:
            return False
        move_bps = abs(now_bbo.mid - past_bbo.mid) / past_bbo.mid * 10_000.0
        return move_bps >= exemption.threshold_bps

    raise TypeError(f"unhandled exemption type: {type(exemption).__name__}")


def evaluate_exemption(rfq: RFQ, config: ObligationsConfig, ctx: ExemptionContext) -> RFQ:
    """Returns `rfq` unchanged if no exemption applies, or a copy with
    is_exempt=True and exemption_type set to the first matching rule."""
    for exemption in config.exemptions:
        if _matches(exemption, rfq, ctx):
            return dataclasses.replace(rfq, is_exempt=True, exemption_type=exemption.type)
    return rfq


@dataclass(slots=True)
class ResponseRatioTracker:
    """§5(1): tracks the response ratio over whatever horizon the caller
    feeds it (day-to-date by default — see PLAN.md Decision D9; the 95%
    floor is nominally per calendar month, but nothing here assumes a
    particular period length)."""

    _presented: int = 0
    _answered: int = 0
    _by_isin: dict[str, list[int]] = field(default_factory=dict)

    def record(self, isin: str, *, is_exempt: bool, was_answered: bool) -> None:
        if is_exempt:
            return  # excluded from the denominator entirely (§5(2))
        self._presented += 1
        counts = self._by_isin.setdefault(isin, [0, 0])
        counts[0] += 1
        if was_answered:
            self._answered += 1
            counts[1] += 1

    @property
    def presented(self) -> int:
        return self._presented

    @property
    def answered(self) -> int:
        return self._answered

    @property
    def ratio(self) -> float:
        return self._answered / self._presented if self._presented else 1.0

    def ratio_for_isin(self, isin: str) -> float:
        presented, answered = self._by_isin.get(isin, [0, 0])
        return answered / presented if presented else 1.0

    def is_breaching(self, min_response_ratio: float) -> bool:
        return self._presented > 0 and self.ratio < min_response_ratio - 1e-12
