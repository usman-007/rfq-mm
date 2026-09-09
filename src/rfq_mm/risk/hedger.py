"""Hedge inventory back to the reference market once it exceeds a trigger
threshold, with a slippage cost (spread-crossing + a documented bps
add-on — illustrative, like engine/quoter.TradingCosts; no public
fee/slippage schedule to source real figures from).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from rfq_mm.core.types import BBO, Side


@dataclass(frozen=True, slots=True)
class HedgeParams:
    slippage_bps: float = 5.0
    trigger_abs_position_eur: float = 150_000.0
    target_abs_position_eur: float = 50_000.0


@dataclass(frozen=True, slots=True)
class HedgeResult:
    isin: str
    ts: datetime
    side: Side  # direction of the hedge trade itself
    units: float
    price: float
    cost_eur: float


def maybe_hedge(
    isin: str, position_units: float, mark_bbo: BBO, ts: datetime, params: HedgeParams
) -> HedgeResult | None:
    position_eur = abs(position_units * mark_bbo.mid)
    if position_eur <= params.trigger_abs_position_eur or position_units == 0:
        return None

    target_units = math.copysign(params.target_abs_position_eur, position_units) / mark_bbo.mid
    hedge_units = position_units - target_units
    if abs(hedge_units) < 1e-9:
        return None

    if hedge_units > 0:  # reduce a long position: hedger sells, crosses the bid
        side = Side.SELL
        price = mark_bbo.bid * (1.0 - params.slippage_bps / 10_000.0)
    else:  # reduce a short position: hedger buys, crosses the ask
        side = Side.BUY
        price = mark_bbo.ask * (1.0 + params.slippage_bps / 10_000.0)

    cost_eur = abs(price - mark_bbo.mid) * abs(hedge_units)
    return HedgeResult(
        isin=isin, ts=ts, side=side, units=abs(hedge_units), price=price, cost_eur=cost_eur
    )
