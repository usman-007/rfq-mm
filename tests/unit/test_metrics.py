from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rfq_mm.analytics.metrics import (
    compute_fill_spread_stats,
    compute_markouts,
    compute_pnl_attribution,
    fill_ratio_by_size_bucket,
    intraday_sharpe,
    inventory_path_stats,
    markout_summary,
    quoted_vs_reference_series,
    size_bucket,
    volume_weighted_effective_spread_bps,
)
from rfq_mm.core.types import RFQ, Fill, Quote, Side
from rfq_mm.engine.rfq import RfqOutcome
from rfq_mm.risk.hedger import HedgeParams, maybe_hedge
from rfq_mm.risk.inventory import InventoryBook

TZ = ZoneInfo("Europe/Berlin")
ISIN = "DE0007164600"
TS = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)


def test_size_bucket_boundaries() -> None:
    assert size_bucket(0.0) == "0-500"
    assert size_bucket(499.0) == "0-500"
    assert size_bucket(500.0) == "500-2000"
    assert size_bucket(15_000.0) == "10000+"


def test_markouts_reflect_injected_drift(feed) -> None:
    price = feed.bbo(TS, ISIN, 1_000.0).ask
    fill = Fill(
        fill_id="f1",
        quote_id="q1",
        ts=TS,
        isin=ISIN,
        side=Side.BUY,
        price=price,
        size_eur=1_000.0,
        requester_id="r1",
        is_informed_label=True,
    )
    feed.apply_informed_drift(ISIN, TS, drift_bps=500.0)
    markouts = compute_markouts([fill], feed, horizons=(60.0,))
    assert len(markouts) == 1
    assert markouts[0].bps > 100.0  # price rose sharply after the client bought


def test_markout_summary_has_expected_shape(feed) -> None:
    price = feed.bbo(TS, ISIN, 1_000.0).ask
    fill = Fill(
        fill_id="f1",
        quote_id="q1",
        ts=TS,
        isin=ISIN,
        side=Side.BUY,
        price=price,
        size_eur=1_000.0,
        requester_id="r1",
        is_informed_label=False,
    )
    markouts = compute_markouts([fill], feed)
    summary = markout_summary(markouts)
    assert set(summary["horizon_seconds"]) == {1.0, 10.0, 60.0, 300.0}


def test_effective_spread_and_volume_weighting(feed) -> None:
    bbo = feed.bbo(TS, ISIN, 1_000.0)
    fill = Fill(
        fill_id="f1",
        quote_id="q1",
        ts=TS,
        isin=ISIN,
        side=Side.BUY,
        price=bbo.ask,
        size_eur=1_000.0,
        requester_id="r1",
        is_informed_label=False,
    )
    stats = compute_fill_spread_stats([fill], feed)
    assert len(stats) == 1
    assert stats[0].effective_spread_bps > 0  # MM sold above mid
    vw = volume_weighted_effective_spread_bps(stats)
    assert vw == stats[0].effective_spread_bps


def test_fill_ratio_by_size_bucket() -> None:
    ttl = TS + timedelta(seconds=10)
    q1 = Quote(
        quote_id="q1",
        rfq_id="r1",
        ts_issued=TS,
        isin=ISIN,
        bid=100,
        ask=101,
        firm_size_eur=300,
        ttl_expiry_ts=ttl,
        is_voluntary=False,
    )
    q2 = Quote(
        quote_id="q2",
        rfq_id="r2",
        ts_issued=TS,
        isin=ISIN,
        bid=100,
        ask=101,
        firm_size_eur=300,
        ttl_expiry_ts=ttl,
        is_voluntary=False,
    )
    q3 = Quote(
        quote_id="q3",
        rfq_id="r3",
        ts_issued=TS,
        isin=ISIN,
        bid=100,
        ask=101,
        firm_size_eur=5_000,
        ttl_expiry_ts=ttl,
        is_voluntary=False,
    )
    ratios = fill_ratio_by_size_bucket([q1, q2, q3], filled_quote_ids={"q1"})
    assert ratios["0-500"] == 0.5
    assert ratios["2000-10000"] == 0.0


def test_inventory_path_stats() -> None:
    snaps = [(TS, 0.0), (TS + timedelta(hours=1), 1_000.0), (TS + timedelta(hours=2), 0.0)]
    stats = inventory_path_stats(snaps)
    assert stats.max_abs_position_eur == 1_000.0
    assert stats.time_weighted_position_eur == 500.0


def test_intraday_sharpe_zero_for_flat_series() -> None:
    assert intraday_sharpe([100.0, 100.0, 100.0, 100.0]) == 0.0


def test_intraday_sharpe_positive_for_noisy_uptrend() -> None:
    # perfectly linear gains have zero variance in the diffs, making the
    # ratio degenerate (correctly 0.0) -- use a noisy-but-net-positive
    # series to exercise a genuinely well-defined positive Sharpe.
    assert intraday_sharpe([0.0, 12.0, 8.0, 25.0, 19.0, 40.0]) > 0.0


def test_pnl_attribution_reconciles_within_a_cent(feed) -> None:
    book = InventoryBook()
    fills = []
    for i, (side, minutes, size) in enumerate(
        [
            (Side.BUY, 0, 1_000.0),
            (Side.SELL, 5, 1_500.0),
            (Side.BUY, 10, 800.0),
            (Side.BUY, 15, 2_000.0),
            (Side.SELL, 20, 1_200.0),
        ]
    ):
        ts = TS + timedelta(minutes=minutes)
        bbo = feed.bbo(ts, ISIN, size)
        price = bbo.ask if side is Side.BUY else bbo.bid
        fill = Fill(
            fill_id=f"f{i}",
            quote_id=f"q{i}",
            ts=ts,
            isin=ISIN,
            side=side,
            price=price,
            size_eur=size,
            requester_id="r1",
            is_informed_label=(i == 0),
        )
        fills.append(fill)
        book.record_fill(fill)

    hedge_ts = TS + timedelta(minutes=25)
    hedge_bbo = feed.bbo(hedge_ts, ISIN, 500.0)
    hedge = maybe_hedge(
        ISIN,
        book.units(ISIN),
        hedge_bbo,
        hedge_ts,
        HedgeParams(trigger_abs_position_eur=0.0, target_abs_position_eur=0.0),
    )
    hedges = []
    if hedge is not None:
        book.apply_hedge(ISIN, hedge.side.sign, hedge.units, hedge.price, hedge.ts)
        hedges.append(hedge)

    final_ts = TS + timedelta(minutes=30)
    final_mark = feed.bbo(final_ts, ISIN, 500.0).mid
    final_units = book.units(ISIN)

    attribution = compute_pnl_attribution(fills, hedges, feed, final_units, final_mark)
    assert attribution.reconciliation_error_eur() < 0.01
    assert hedges  # sanity: the hedge path was actually exercised by this test


def _rfq(rfq_id: str = "rfq1") -> RFQ:
    return RFQ(
        rfq_id=rfq_id,
        ts=TS,
        isin=ISIN,
        requester_id="r1",
        side=Side.BUY,
        requested_size_eur=1_000.0,
        is_exempt=False,
    )


def test_quoted_vs_reference_series_computes_bps_and_clamp_flag() -> None:
    quote = Quote(
        quote_id="q1",
        rfq_id="rfq1",
        ts_issued=TS,
        isin=ISIN,
        bid=99.9,
        ask=100.1,  # spread = 0.2, mid = 100.0 -> 20 bps
        firm_size_eur=1_000.0,
        ttl_expiry_ts=TS + timedelta(seconds=10),
        is_voluntary=False,
    )
    clamped_outcome = RfqOutcome(
        rfq=_rfq(),
        quote=quote,
        live_quote=None,
        reference_spread=0.3,  # 30 bps reference
        regulatory_cap=0.2,  # cap == quoted spread -> was_clamped
    )
    no_quote_outcome = RfqOutcome(rfq=_rfq("rfq2"), quote=None, live_quote=None)

    df = quoted_vs_reference_series([clamped_outcome, no_quote_outcome])

    assert len(df) == 1  # the no-quote outcome is skipped
    row = df.iloc[0]
    assert row["quoted_spread_bps"] == pytest.approx(20.0)
    assert row["reference_spread_bps"] == pytest.approx(30.0)
    assert row["cap_bps"] == pytest.approx(20.0)
    assert bool(row["was_clamped"]) is True


def test_quoted_vs_reference_series_unclamped_when_below_cap() -> None:
    quote = Quote(
        quote_id="q1",
        rfq_id="rfq1",
        ts_issued=TS,
        isin=ISIN,
        bid=99.95,
        ask=100.05,  # spread = 0.1, mid = 100.0 -> 10 bps
        firm_size_eur=1_000.0,
        ttl_expiry_ts=TS + timedelta(seconds=10),
        is_voluntary=False,
    )
    outcome = RfqOutcome(
        rfq=_rfq(), quote=quote, live_quote=None, reference_spread=0.3, regulatory_cap=0.2
    )

    df = quoted_vs_reference_series([outcome])

    assert bool(df.iloc[0]["was_clamped"]) is False


def test_quoted_vs_reference_series_empty_input() -> None:
    df = quoted_vs_reference_series([])
    assert df.empty
