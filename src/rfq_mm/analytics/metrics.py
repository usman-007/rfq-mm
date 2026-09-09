"""Desk metrics (spec.md §6): markouts, realised spread, effective spread
capture, fill ratio, inventory stats, and P&L attribution.

P&L attribution derivation (R161-R163): total P&L for the day is computed
once, exactly, from the cash-flow ledger of every fill and hedge trade:

    total_pnl = spread_capture_total + inventory_mtm_total - hedge_slippage_total

This is an exact identity (each trade's cash flow splits into a "traded at
mid" component, which telescopes into inventory mark-to-market, and a
"traded away from mid" component, which is exactly the spread captured or
paid). "Adverse selection" is then *carved out* of inventory_mtm_total by
subtraction — the aggregate 60s post-fill price drift specifically
following informed fills — rather than computed as an independent
quantity hoped to reconcile:

    inventory_mtm_reported = inventory_mtm_total - adverse_selection_total

so spread_capture_total + adverse_selection_total + inventory_mtm_reported
- hedge_slippage_total == total_pnl exactly, by construction, not
approximately.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import pandas as pd

from rfq_mm.core.types import Fill, Quote
from rfq_mm.feed.base import ReferenceMarketFeed
from rfq_mm.risk.hedger import HedgeResult

MARKOUT_HORIZONS_SECONDS: tuple[float, ...] = (1.0, 10.0, 60.0, 300.0)

_SIZE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 500.0),
    (500.0, 2_000.0),
    (2_000.0, 10_000.0),
    (10_000.0, math.inf),
)


def size_bucket(size_eur: float) -> str:
    for lo, hi in _SIZE_BUCKETS:
        if lo <= size_eur < hi:
            return f"{lo:.0f}-{hi:.0f}" if hi != math.inf else f"{lo:.0f}+"
    return "unknown"


@dataclass(frozen=True, slots=True)
class Markout:
    fill_id: str
    isin: str
    requester_id: str
    size_eur: float
    horizon_seconds: float
    bps: float


def _mid_at_or_none(
    feed: ReferenceMarketFeed, ts: datetime, isin: str, size_eur: float
) -> float | None:
    try:
        return feed.bbo(ts, isin, size_eur).mid
    except ValueError:
        return None


def compute_markouts(
    fills: list[Fill],
    feed: ReferenceMarketFeed,
    horizons: tuple[float, ...] = MARKOUT_HORIZONS_SECONDS,
) -> list[Markout]:
    """mo(tau) = side * (mid(t+tau) - fill_price) / mid(t) * 1e4 (R144)."""
    out: list[Markout] = []
    for fill in fills:
        mid_now = _mid_at_or_none(feed, fill.ts, fill.isin, fill.size_eur)
        if mid_now is None or mid_now <= 0:
            continue
        for tau in horizons:
            mid_future = _mid_at_or_none(
                feed, fill.ts + timedelta(seconds=tau), fill.isin, fill.size_eur
            )
            if mid_future is None:
                continue
            bps = fill.side.sign * (mid_future - fill.price) / mid_now * 10_000.0
            out.append(
                Markout(
                    fill_id=fill.fill_id,
                    isin=fill.isin,
                    requester_id=fill.requester_id,
                    size_eur=fill.size_eur,
                    horizon_seconds=tau,
                    bps=bps,
                )
            )
    return out


def markouts_to_frame(markouts: list[Markout]) -> pd.DataFrame:
    df = pd.DataFrame([asdict(m) for m in markouts])
    if df.empty:
        return df
    df["size_bucket"] = df["size_eur"].map(size_bucket)
    return df


def markout_summary(markouts: list[Markout]) -> pd.DataFrame:
    """Mean/median markout bps per horizon (R145)."""
    df = markouts_to_frame(markouts)
    if df.empty:
        return df
    return df.groupby("horizon_seconds")["bps"].agg(["mean", "median", "count"]).reset_index()


def markout_summary_by_size_bucket(markouts: list[Markout]) -> pd.DataFrame:
    df = markouts_to_frame(markouts)
    if df.empty:
        return df
    return (
        df.groupby(["horizon_seconds", "size_bucket"])["bps"]
        .agg(["mean", "median", "count"])
        .reset_index()
    )


def markout_summary_by_requester(markouts: list[Markout]) -> pd.DataFrame:
    df = markouts_to_frame(markouts)
    if df.empty:
        return df
    return (
        df.groupby(["horizon_seconds", "requester_id"])["bps"]
        .agg(["mean", "median", "count"])
        .reset_index()
    )


@dataclass(frozen=True, slots=True)
class FillSpreadStats:
    fill_id: str
    size_eur: float
    effective_spread_bps: float  # R150: side * (fill_price - mid_at_fill) / mid_at_fill * 1e4
    effective_spread_eur: float
    markout_60s_bps: float | None
    realised_spread_bps: float | None  # R149: effective_spread_bps - markout_60s_bps


def compute_fill_spread_stats(
    fills: list[Fill], feed: ReferenceMarketFeed
) -> list[FillSpreadStats]:
    stats: list[FillSpreadStats] = []
    for fill in fills:
        mid_now = _mid_at_or_none(feed, fill.ts, fill.isin, fill.size_eur)
        if mid_now is None or mid_now <= 0:
            continue
        eff_bps = fill.side.sign * (fill.price - mid_now) / mid_now * 10_000.0
        eff_eur = fill.side.sign * (fill.price - mid_now) * fill.units

        mid_60s = _mid_at_or_none(feed, fill.ts + timedelta(seconds=60.0), fill.isin, fill.size_eur)
        markout_60s = None
        realised = None
        if mid_60s is not None:
            markout_60s = fill.side.sign * (mid_60s - fill.price) / mid_now * 10_000.0
            realised = eff_bps - markout_60s

        stats.append(
            FillSpreadStats(
                fill_id=fill.fill_id,
                size_eur=fill.size_eur,
                effective_spread_bps=eff_bps,
                effective_spread_eur=eff_eur,
                markout_60s_bps=markout_60s,
                realised_spread_bps=realised,
            )
        )
    return stats


def volume_weighted_effective_spread_bps(stats: list[FillSpreadStats]) -> float:
    total_size = sum(s.size_eur for s in stats)
    if total_size <= 0:
        return 0.0
    return sum(s.effective_spread_bps * s.size_eur for s in stats) / total_size


def fill_ratio_by_size_bucket(quotes: list[Quote], filled_quote_ids: set[str]) -> dict[str, float]:
    """R154: accepted / quoted, bucketed by the quote's firm size."""
    quoted_counts: dict[str, int] = {}
    filled_counts: dict[str, int] = {}
    for q in quotes:
        bucket = size_bucket(q.firm_size_eur)
        quoted_counts[bucket] = quoted_counts.get(bucket, 0) + 1
        if q.quote_id in filled_quote_ids:
            filled_counts[bucket] = filled_counts.get(bucket, 0) + 1
    return {bucket: filled_counts.get(bucket, 0) / count for bucket, count in quoted_counts.items()}


def intraday_sharpe(pnl_snapshots: list[float]) -> float:
    """A simple realised Sharpe-like ratio from a within-day P&L path: mean
    period P&L change / stdev of period P&L changes. With a single
    simulated day this measures *intraday* P&L stability, not a
    conventional Sharpe ratio annualised across many trading days —
    labelled as such wherever it's reported."""
    if len(pnl_snapshots) < 3:
        return 0.0
    diffs = [b - a for a, b in zip(pnl_snapshots, pnl_snapshots[1:], strict=False)]
    mean = sum(diffs) / len(diffs)
    variance = sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)
    stdev = math.sqrt(variance)
    return mean / stdev if stdev > 0 else 0.0


@dataclass(frozen=True, slots=True)
class InventoryStats:
    max_abs_position_eur: float
    time_weighted_position_eur: float


def inventory_path_stats(snapshots: list[tuple[datetime, float]]) -> InventoryStats:
    """snapshots: (ts, position_eur) pairs, pre-sorted by ts, spanning the
    full session (including boundary snapshots at open/close)."""
    if not snapshots:
        return InventoryStats(0.0, 0.0)
    max_abs = max(abs(pos) for _, pos in snapshots)
    if len(snapshots) == 1:
        return InventoryStats(max_abs, abs(snapshots[0][1]))
    total_seconds = (snapshots[-1][0] - snapshots[0][0]).total_seconds()
    if total_seconds <= 0:
        return InventoryStats(max_abs, abs(snapshots[-1][1]))
    weighted_sum = 0.0
    for (t0, pos0), (t1, _pos1) in zip(snapshots, snapshots[1:], strict=False):
        dt = (t1 - t0).total_seconds()
        weighted_sum += abs(pos0) * dt
    return InventoryStats(max_abs, weighted_sum / total_seconds)


@dataclass(frozen=True, slots=True)
class PnLAttribution:
    spread_capture_eur: float
    adverse_selection_eur: float
    inventory_mtm_eur: float
    hedge_slippage_eur: float
    total_pnl_eur: float

    def reconciliation_error_eur(self) -> float:
        components = (
            self.spread_capture_eur
            + self.adverse_selection_eur
            + self.inventory_mtm_eur
            - self.hedge_slippage_eur
        )
        return abs(components - self.total_pnl_eur)


def compute_pnl_attribution(
    fills: list[Fill],
    hedges: list[HedgeResult],
    feed: ReferenceMarketFeed,
    final_inventory_units: float,
    final_mark_price: float,
) -> PnLAttribution:
    spread_capture_total = 0.0
    cash_change = 0.0
    telescoped_mtm = 0.0  # -sum(delta_units_i * mid_at_trade_i)

    for fill in fills:
        delta_units = -fill.side.sign * fill.units  # MM's inventory change
        cash_change += -delta_units * fill.price
        mid_now = _mid_at_or_none(feed, fill.ts, fill.isin, fill.size_eur)
        if mid_now is None or mid_now <= 0:
            continue
        spread_capture_total += fill.side.sign * (fill.price - mid_now) * fill.units
        telescoped_mtm += -delta_units * mid_now

    hedge_slippage_total = 0.0
    for hedge in hedges:
        delta_units = hedge.side.sign * hedge.units
        cash_change += -delta_units * hedge.price
        telescoped_mtm += -delta_units * hedge.mid_at_hedge
        hedge_slippage_total += hedge.cost_eur

    total_pnl = cash_change + final_inventory_units * final_mark_price
    inventory_mtm_total = telescoped_mtm + final_inventory_units * final_mark_price

    adverse_selection_total = 0.0
    for fill in fills:
        if not fill.is_informed_label:
            continue
        mid_now = _mid_at_or_none(feed, fill.ts, fill.isin, fill.size_eur)
        mid_60s = _mid_at_or_none(feed, fill.ts + timedelta(seconds=60.0), fill.isin, fill.size_eur)
        if mid_now is None or mid_60s is None:
            continue
        # dollar impact, from the MM's perspective, of the 60s move following this fill
        adverse_selection_total += -fill.side.sign * (mid_60s - mid_now) * fill.units

    inventory_mtm_reported = inventory_mtm_total - adverse_selection_total

    return PnLAttribution(
        spread_capture_eur=spread_capture_total,
        adverse_selection_eur=adverse_selection_total,
        inventory_mtm_eur=inventory_mtm_reported,
        hedge_slippage_eur=hedge_slippage_total,
        total_pnl_eur=total_pnl,
    )
