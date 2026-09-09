"""Inventory limits and kill switch.

engine/quoter.py takes `inventory_units` and `inventory_headroom_eur` as
plain parameters rather than depending on this module directly (S5 was
built before this one) — the caller (the simulation runner) is expected to
read both from an InventoryBook before calling into the quote pipeline.

The smoke-test during S5 development showed *why* this module matters:
without a hard position limit, reservation-price skew is unbounded and can
push quotes to nonsensical (even negative) prices as inventory runs away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from rfq_mm.core.types import Fill, Position


@dataclass(frozen=True, slots=True)
class InventoryLimits:
    max_abs_position_eur: float = 300_000.0
    kill_switch_abs_position_eur: float = 500_000.0


def _update_position(
    pos: Position | None, isin: str, delta_units: float, fill_price: float, ts: datetime
) -> Position:
    if pos is None or pos.quantity_units == 0:
        return Position(isin=isin, quantity_units=delta_units, avg_price=fill_price, ts=ts)

    new_qty = pos.quantity_units + delta_units
    increasing = (pos.quantity_units > 0) == (delta_units > 0)
    if increasing:
        total_cost = pos.quantity_units * pos.avg_price + delta_units * fill_price
        avg = total_cost / new_qty if new_qty != 0 else fill_price
        return Position(isin=isin, quantity_units=new_qty, avg_price=avg, ts=ts)

    if new_qty == 0:
        return Position(isin=isin, quantity_units=0.0, avg_price=fill_price, ts=ts)
    if (new_qty > 0) == (pos.quantity_units > 0):
        # still reducing the same-direction position: cost basis is unchanged
        return Position(isin=isin, quantity_units=new_qty, avg_price=pos.avg_price, ts=ts)
    # flipped sign: the new exposure's basis starts fresh at the fill price
    return Position(isin=isin, quantity_units=new_qty, avg_price=fill_price, ts=ts)


@dataclass(slots=True)
class InventoryBook:
    limits: InventoryLimits = field(default_factory=InventoryLimits)
    _positions: dict[str, Position] = field(default_factory=dict)

    def position(self, isin: str) -> Position | None:
        return self._positions.get(isin)

    def units(self, isin: str) -> float:
        pos = self._positions.get(isin)
        return pos.quantity_units if pos else 0.0

    def position_eur(self, isin: str, mark_price: float) -> float:
        return self.units(isin) * mark_price

    def headroom_eur(self, isin: str, mark_price: float) -> float:
        current = abs(self.position_eur(isin, mark_price))
        return max(0.0, self.limits.max_abs_position_eur - current)

    def is_killed(self, isin: str, mark_price: float) -> bool:
        return abs(self.position_eur(isin, mark_price)) >= self.limits.kill_switch_abs_position_eur

    def record_fill(self, fill: Fill) -> None:
        # MM inventory moves opposite the client's side: a client BUY means
        # the MM sells, so MM inventory decreases.
        delta_units = -fill.side.sign * fill.units
        current = self._positions.get(fill.isin)
        self._positions[fill.isin] = _update_position(
            current, fill.isin, delta_units, fill.price, fill.ts
        )

    def apply_hedge(
        self, isin: str, hedge_side_sign: int, units: float, price: float, ts: datetime
    ) -> None:
        """A hedge trade also moves the book — hedge_side_sign is +1 if the
        MM bought (reducing a short) and -1 if the MM sold (reducing a long)."""
        current = self._positions.get(isin)
        self._positions[isin] = _update_position(current, isin, hedge_side_sign * units, price, ts)
