"""ReferenceMarketFeed protocol (spec.md §2) — the one interface every feed
tier (synthetic, bars, real_l1) implements, so the rest of the engine never
knows which tier is behind it."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from rfq_mm.core.types import BBO


class ReferenceMarketFeed(Protocol):
    def bbo(self, ts: datetime, isin: str, size_eur: float) -> BBO: ...
