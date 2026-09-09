"""§12 requester-behaviour surveillance.

Two of the three named abuse patterns have a real signal in this proxy:

- high-frequency quote requests: a sliding 1-minute per-requester count
  against surveillance.max_rfq_per_participant_per_minute.
- mass enter-then-cancel: we have no order-cancel messages in an RFQ
  model, so this is proxied as the fraction of a requester's RFQs that
  never resulted in a fill, against surveillance.max_cancel_ratio.

"Manipulating the reference market to move the quote" is not implemented:
this simulator has no independent view of who moves the reference price
(the feed is either synthetic or replayed historical data), so there is
no signal to detect it from. Documented as a limitation, not silently
skipped.

Flagged requester IDs from detect_high_frequency_requesters are meant to
feed venue.obligations.ExemptionContext.flagged_manipulation_requesters
for the suspected_manipulation exemption — surveillance output driving a
quoting-duty exemption, matching the spec's own cross-reference.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from rfq_mm.core.types import RFQ


@dataclass(frozen=True, slots=True)
class SurveillanceFlag:
    requester_id: str
    category: str
    detail: str


def detect_high_frequency_requesters(
    rfqs: list[RFQ], max_per_minute: int
) -> list[SurveillanceFlag]:
    """`rfqs` must be pre-sorted by ts (as flow.generator already returns them)."""
    flags: list[SurveillanceFlag] = []
    flagged_ids: set[str] = set()
    windows: dict[str, deque[datetime]] = defaultdict(deque)

    for rfq in rfqs:
        window = windows[rfq.requester_id]
        window.append(rfq.ts)
        cutoff = rfq.ts - timedelta(minutes=1)
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) > max_per_minute and rfq.requester_id not in flagged_ids:
            flagged_ids.add(rfq.requester_id)
            flags.append(
                SurveillanceFlag(
                    rfq.requester_id, "high_frequency", f"{len(window)} requests within 1 minute"
                )
            )
    return flags


def detect_high_cancel_ratio(
    rfqs: list[RFQ],
    filled_rfq_ids: set[str],
    max_cancel_ratio: float,
    min_requests: int = 5,
) -> list[SurveillanceFlag]:
    by_requester: dict[str, list[RFQ]] = defaultdict(list)
    for rfq in rfqs:
        by_requester[rfq.requester_id].append(rfq)

    flags: list[SurveillanceFlag] = []
    for requester_id, requests in by_requester.items():
        if len(requests) < min_requests:
            continue
        n_unfilled = sum(1 for r in requests if r.rfq_id not in filled_rfq_ids)
        ratio = n_unfilled / len(requests)
        if ratio > max_cancel_ratio:
            flags.append(
                SurveillanceFlag(
                    requester_id,
                    "high_cancel_ratio",
                    f"{ratio:.2f} unfilled ratio over {len(requests)} requests",
                )
            )
    return flags
