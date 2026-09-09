"""Quote pricing: fair value, Avellaneda-Stoikov reservation price and risk
spread, the §7(1) regulatory clamp, and size (spec.md §5 steps 2-4, 5, 7).

Formula convention note: sigma here is estimated as an *absolute* price
volatility (stdev of price differences, scaled to a full-session horizon),
and gamma/kappa are treated as tunable dimensionless constants calibrated
per instrument price scale — the standard, dimensionally-informal way this
exact textbook formula is implemented in practice (it's a heuristic
spread-widening formula, not a rigorously unit-checked physical model).
The spec explicitly asks to keep the closed form rather than solve the
HJB numerically, so that convention is followed here rather than
"corrected".

Tick rounding (step 6) is core.ticks.round_quote — not repeated here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rfq_mm.core.types import BBO, Instrument
from rfq_mm.feed.base import ReferenceMarketFeed
from rfq_mm.venue.config import GuaranteedVolumesConfig, SpreadConfig
from rfq_mm.venue.guarantees import guaranteed_volume_for


@dataclass(frozen=True, slots=True)
class QuoterParams:
    gamma: float = 0.1  # risk aversion
    kappa: float = 1.5  # A-S order-arrival intensity decay
    use_micro_price: bool = False
    per_quote_cap_eur: float = 200_000.0
    adverse_selection_scale: float = 1.0  # multiplies the tracked recent markout (bps)
    sigma_lookback_seconds: float = 1800.0
    sigma_samples: int = 10


@dataclass(frozen=True, slots=True)
class TradingCosts:
    """Illustrative bps costs applied in the §7(1) clamp ("after trading,
    settlement and FX costs"). The spec names the cost *categories*
    (spread.include_costs in the venue YAML) but not their magnitudes —
    there is no public fee schedule to source these from, so they are a
    documented, clearly-labelled assumption, not a verified figure."""

    trading_cost_bps: float = 2.0
    settlement_cost_bps: float = 1.0
    fx_cost_bps: float = 3.0


@dataclass(slots=True)
class MarkoutTracker:
    """EWMA of recent realised (client-signed) markout in bps, per ISIN,
    feeding the adverse-selection premium (R130). Positive = adverse to
    the MM. Fed by engine/rfq.py at each fill's short-horizon markout."""

    alpha: float = 0.2
    _ewma_bps: dict[str, float] = field(default_factory=dict)

    def observe(self, isin: str, markout_bps: float) -> None:
        prev = self._ewma_bps.get(isin, 0.0)
        self._ewma_bps[isin] = self.alpha * markout_bps + (1.0 - self.alpha) * prev

    def current(self, isin: str) -> float:
        return self._ewma_bps.get(isin, 0.0)


def fair_value(bbo: BBO, *, use_micro_price: bool) -> float:
    return bbo.micro_price if use_micro_price else bbo.mid


def estimate_sigma(
    feed: ReferenceMarketFeed,
    isin: str,
    ts: datetime,
    session_seconds: float,
    *,
    lookback_seconds: float = 1800.0,
    n_samples: int = 10,
) -> float:
    """Absolute price-difference stdev, scaled (via sqrt-time) to a
    full-session-equivalent volatility for use directly with
    t_remaining_frac in [0, 1]."""
    step = lookback_seconds / n_samples
    prices = []
    for i in range(n_samples + 1):
        sample_ts = ts - timedelta(seconds=lookback_seconds) + timedelta(seconds=i * step)
        try:
            prices.append(feed.bbo(sample_ts, isin, 1_000.0).mid)
        except ValueError:
            continue
    if len(prices) < 3:
        return 0.0
    diffs = [b - a for a, b in zip(prices, prices[1:], strict=False)]
    mean_diff = sum(diffs) / len(diffs)
    variance = sum((d - mean_diff) ** 2 for d in diffs) / (len(diffs) - 1)
    stdev_per_step = math.sqrt(variance)
    stdev_per_second = stdev_per_step / math.sqrt(step)
    return stdev_per_second * math.sqrt(session_seconds)


def reservation_price(
    fv: float, inventory_units: float, gamma: float, sigma: float, t_remaining_frac: float
) -> float:
    """r = fv - q*gamma*sigma^2*(T-t) (spec.md §5 step 3)."""
    return fv - inventory_units * gamma * sigma**2 * t_remaining_frac


def risk_spread_width(gamma: float, sigma: float, t_remaining_frac: float, kappa: float) -> float:
    """delta = gamma*sigma^2*(T-t) + (2/gamma)*ln(1+gamma/kappa) (spec.md §5 step 4).
    This is the *total* bid-ask width; bid = r - delta/2, ask = r + delta/2."""
    return gamma * sigma**2 * t_remaining_frac + (2.0 / gamma) * math.log(1.0 + gamma / kappa)


def cost_allowance_price(
    spread_config: SpreadConfig, costs: TradingCosts, mid: float, *, is_foreign: bool
) -> float:
    bps = 0.0
    if "trading" in spread_config.include_costs:
        bps += costs.trading_cost_bps
    if "settlement" in spread_config.include_costs:
        bps += costs.settlement_cost_bps
    if is_foreign and "fx" in spread_config.include_costs:
        bps += costs.fx_cost_bps
    return bps / 10_000.0 * mid


@dataclass(frozen=True, slots=True)
class QuoteDecision:
    """Everything downstream needs: the pre-tick-rounding price/size
    decision, plus the diagnostics that make the clamp tension visible
    (R133/R134)."""

    reservation_price: float
    risk_delta: float
    regulatory_cap: float | None  # None when unclamped (voluntary/discretionary path)
    quoted_delta: float
    was_clamped: bool
    is_voluntary_path: bool  # exempt RFQ or below-price discretionary instrument
    firm_size_eur: float
    raw_bid: float
    raw_ask: float

    @property
    def can_quote(self) -> bool:
        return self.quoted_delta > 0 and self.firm_size_eur > 0


def price_quote(
    *,
    bbo: BBO,
    instrument: Instrument,
    inventory_units: float,
    inventory_headroom_eur: float,
    is_exempt: bool,
    guaranteed_volumes: GuaranteedVolumesConfig,
    spread_config: SpreadConfig,
    costs: TradingCosts,
    params: QuoterParams,
    markout_tracker: MarkoutTracker,
    sigma: float,
    t_remaining_frac: float,
) -> QuoteDecision:
    fv = fair_value(bbo, use_micro_price=params.use_micro_price)
    r = reservation_price(fv, inventory_units, params.gamma, sigma, t_remaining_frac)

    adverse_premium_bps = max(0.0, markout_tracker.current(instrument.isin)) * (
        params.adverse_selection_scale
    )
    adverse_premium_price = adverse_premium_bps / 10_000.0 * fv
    risk_delta = (
        risk_spread_width(params.gamma, sigma, t_remaining_frac, params.kappa)
        + 2.0 * adverse_premium_price
    )

    guarantee = guaranteed_volume_for(
        guaranteed_volumes, instrument, reference_market_open=bbo.is_open, price=fv
    )
    is_voluntary_path = is_exempt or guarantee.is_discretionary

    if is_voluntary_path:
        quoted_delta = risk_delta
        was_clamped = False
        regulatory_cap = None
        size_cap = min(inventory_headroom_eur, params.per_quote_cap_eur)
    else:
        is_foreign = instrument.fx_rate_to_eur is not None
        cap = bbo.spread + cost_allowance_price(spread_config, costs, fv, is_foreign=is_foreign)
        was_clamped = risk_delta > cap
        quoted_delta = min(risk_delta, cap)
        regulatory_cap = cap
        size_cap = min(
            guarantee.guaranteed_volume_eur, inventory_headroom_eur, params.per_quote_cap_eur
        )

    size_cap = max(0.0, size_cap)
    raw_bid = r - quoted_delta / 2.0
    raw_ask = r + quoted_delta / 2.0

    return QuoteDecision(
        reservation_price=r,
        risk_delta=risk_delta,
        regulatory_cap=regulatory_cap,
        quoted_delta=quoted_delta,
        was_clamped=was_clamped,
        is_voluntary_path=is_voluntary_path,
        firm_size_eur=size_cap,
        raw_bid=raw_bid,
        raw_ask=raw_ask,
    )
