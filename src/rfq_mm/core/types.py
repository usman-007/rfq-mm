"""Foundational, venue-agnostic data types.

Frozen dataclasses per spec.md S3 (R114a): Quote, RFQ, Fill, BBO, Position.
`Instrument` and `Segment` live here too since every other layer depends on them
and they must not create a dependency on the `venue` package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        """+1 for buy (client buys, MM sells), -1 for sell."""
        return 1 if self is Side.BUY else -1


class Segment(StrEnum):
    """§7(1) guaranteed-volume segments, matching venues/eix.yaml exactly."""

    DAX = "DAX"
    MDAX = "MDAX"
    SDAX = "SDAX"
    TECDAX = "TecDAX"
    DOMESTIC_OTHER = "DOMESTIC_OTHER"
    DJ30 = "DJ30"
    US_OTHER = "US_OTHER"
    ESTX50 = "ESTX50"
    FOREIGN_OTHER = "FOREIGN_OTHER"
    ETF_DAX = "ETF_DAX"
    ETF_OTHER = "ETF_OTHER"
    ETC = "ETC"
    ETP = "ETP"
    FUND = "FUND"


class QuoteStatus(StrEnum):
    OPEN = "open"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class Instrument:
    """A tradable instrument in the curated proxy universe (see instruments/universe.py).

    liquidity_band is a manually assigned RTS 11 ADNT band (1-6, 6 = most liquid),
    since we have no real transaction-history feed to compute ADNT from. ETFs whose
    underlying is exclusively in-scope shares are required by RTS 11 Art. 2(3)-(4)
    to sit in band 6 regardless of their own ADNT.
    """

    isin: str
    name: str
    segment: Segment
    currency: str
    liquidity_band: int
    is_leveraged: bool = False
    fx_rate_to_eur: float | None = None  # None => already EUR-denominated

    def __post_init__(self) -> None:
        if not 1 <= self.liquidity_band <= 6:
            raise ValueError(f"liquidity_band must be 1-6, got {self.liquidity_band}")


@dataclass(frozen=True, slots=True)
class BBO:
    """Best bid/offer from a ReferenceMarketFeed. Sizes are notional EUR."""

    ts: datetime
    isin: str
    bid: float
    ask: float
    bid_size_eur: float
    ask_size_eur: float
    is_open: bool
    is_indicative: bool

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError("bid/ask must be positive")
        if self.bid > self.ask:
            raise ValueError(f"crossed BBO: bid={self.bid} > ask={self.ask}")

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def micro_price(self) -> float:
        """Depth-weighted fair value (spec.md §5 step 2)."""
        denom = self.bid_size_eur + self.ask_size_eur
        if denom <= 0:
            return self.mid
        return (self.bid * self.ask_size_eur + self.ask * self.bid_size_eur) / denom


@dataclass(frozen=True, slots=True)
class RFQ:
    rfq_id: str
    ts: datetime
    isin: str
    requester_id: str
    side: Side
    requested_size_eur: float
    is_exempt: bool
    is_informed: bool = False
    exemption_type: str | None = None

    def __post_init__(self) -> None:
        if self.requested_size_eur <= 0:
            raise ValueError("requested_size_eur must be positive")
        if self.is_exempt and self.exemption_type is None:
            raise ValueError("is_exempt=True requires exemption_type")


@dataclass(frozen=True, slots=True)
class Quote:
    """§6: ISIN + bid + ask + firm size. Immutable once issued; live budget/status
    tracking (remaining_firm_size_eur, status) lives in engine.rfq.LiveQuote."""

    quote_id: str
    rfq_id: str
    ts_issued: datetime
    isin: str
    bid: float
    ask: float
    firm_size_eur: float
    ttl_expiry_ts: datetime
    is_voluntary: bool

    def __post_init__(self) -> None:
        if self.bid >= self.ask:
            raise ValueError(f"crossed quote: bid={self.bid} >= ask={self.ask}")
        if self.firm_size_eur <= 0:
            raise ValueError("firm_size_eur must be positive")

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    quote_id: str
    ts: datetime
    isin: str
    side: Side
    price: float
    size_eur: float
    requester_id: str
    is_informed_label: bool
    withdrawn: bool = False

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.size_eur <= 0:
            raise ValueError("size_eur must be positive")

    @property
    def units(self) -> float:
        """Traded quantity in shares/units, for inventory tracking."""
        return self.size_eur / self.price


@dataclass(frozen=True, slots=True)
class Position:
    isin: str
    quantity_units: float
    avg_price: float
    ts: datetime
