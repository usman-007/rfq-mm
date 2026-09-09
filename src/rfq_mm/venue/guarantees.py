"""§7(1)-(2) guaranteed-volume lookup: segment/state table, leveraged
scaling, and the below-price discretionary threshold.

The raw segment->{ref_open,ref_closed} table lookup lives on
GuaranteedVolumesConfig itself (venue/config.py); this module adds the
instrument-aware logic on top: leveraged scaling and the below-price
discretionary carve-out.

"Above the guaranteed volume, quoting is discretionary" (§7(2)) is not a
separate code path here — engine/quoter.py enforces it structurally by
capping quoted size at the guaranteed volume (R139's min()), so there is
never a quote issued as an *obligation* beyond that figure.
"""

from __future__ import annotations

from dataclasses import dataclass

from rfq_mm.core.types import Instrument
from rfq_mm.venue.config import GuaranteedVolumesConfig


@dataclass(frozen=True, slots=True)
class GuaranteeResult:
    is_discretionary: bool
    guaranteed_volume_eur: float


def guaranteed_volume_for(
    config: GuaranteedVolumesConfig,
    instrument: Instrument,
    *,
    reference_market_open: bool,
    price: float,
) -> GuaranteeResult:
    if price < config.discretionary_below_price_eur:
        return GuaranteeResult(is_discretionary=True, guaranteed_volume_eur=0.0)
    volume = config.volume_for(instrument.segment, reference_market_open=reference_market_open)
    if instrument.is_leveraged:
        volume *= config.leveraged_scaling
    return GuaranteeResult(is_discretionary=False, guaranteed_volume_eur=volume)
