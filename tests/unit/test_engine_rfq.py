from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from numpy.random import default_rng

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.types import RFQ, Instrument, Quote, QuoteStatus, Side
from rfq_mm.engine.quoter import MarkoutTracker, QuoterParams, TradingCosts
from rfq_mm.engine.rfq import ComplianceLog, LiveQuote, accept_quote, decide_acceptance, process_rfq
from rfq_mm.feed.synthetic import SyntheticFeed
from rfq_mm.venue.config import VenueConfig
from rfq_mm.venue.obligations import ResponseRatioTracker

TZ = ZoneInfo("Europe/Berlin")
ISIN = "DE0007164600"


def make_rfq(
    ts: datetime, size: float = 1_000.0, side: Side = Side.BUY, requester: str = "req-00001"
) -> RFQ:
    return RFQ(
        rfq_id=f"rfq-{ts.isoformat()}",
        ts=ts,
        isin=ISIN,
        requester_id=requester,
        side=side,
        requested_size_eur=size,
        is_exempt=False,
    )


def _process(
    rfq: RFQ,
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: SyntheticFeed,
    instrument: Instrument,
    response_tracker: ResponseRatioTracker | None = None,
):
    return process_rfq(
        rfq,
        venue_config=venue_config,
        venue_session=venue_session,
        reference_session=reference_session,
        feed=feed,
        instrument=instrument,
        inventory_units=0.0,
        inventory_headroom_eur=1_000_000.0,
        quoter_params=QuoterParams(),
        costs=TradingCosts(),
        markout_tracker=MarkoutTracker(),
        response_tracker=response_tracker or ResponseRatioTracker(),
        compliance_log=ComplianceLog.empty(),
    )


def test_process_rfq_produces_a_quote(
    venue_config, venue_session, reference_session, feed, instrument
) -> None:
    rfq = make_rfq(datetime(2026, 9, 7, 13, 0, tzinfo=TZ))
    outcome = _process(rfq, venue_config, venue_session, reference_session, feed, instrument)
    assert outcome.quote is not None
    assert outcome.quote.bid < outcome.quote.ask
    assert outcome.quote.firm_size_eur > 0
    assert outcome.live_quote is not None
    assert outcome.live_quote.status == QuoteStatus.OPEN


def test_process_rfq_records_response_ratio(
    venue_config, venue_session, reference_session, feed, instrument
) -> None:
    rfq = make_rfq(datetime(2026, 9, 7, 13, 0, tzinfo=TZ))
    tracker = ResponseRatioTracker()
    _process(rfq, venue_config, venue_session, reference_session, feed, instrument, tracker)
    assert tracker.presented == 1
    assert tracker.answered == 1


def test_exempt_rfq_excluded_from_response_ratio(
    venue_config, venue_session, reference_session, feed, instrument
) -> None:
    rfq = make_rfq(datetime(2026, 9, 7, 8, 5, tzinfo=TZ))  # after_venue_open window
    tracker = ResponseRatioTracker()
    outcome = _process(
        rfq, venue_config, venue_session, reference_session, feed, instrument, tracker
    )
    assert outcome.rfq.is_exempt
    assert tracker.presented == 0


def test_accept_quote_produces_fill(
    venue_config, venue_session, reference_session, feed, instrument
) -> None:
    ts = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    rfq = make_rfq(ts)
    outcome = _process(rfq, venue_config, venue_session, reference_session, feed, instrument)
    live_quote = outcome.live_quote
    assert live_quote is not None
    fill = accept_quote(
        live_quote,
        ts + timedelta(seconds=2),
        500.0,
        Side.BUY,
        "req-00001",
        feed=feed,
        venue_config=venue_config,
        is_informed=False,
        informed_drift_bps=15.0,
        markout_tracker=MarkoutTracker(),
        compliance_log=ComplianceLog.empty(),
    )
    assert fill is not None
    assert fill.price == live_quote.quote.ask
    assert fill.size_eur == 500.0
    assert live_quote.remaining_firm_size_eur == live_quote.quote.firm_size_eur - 500.0


def test_accept_after_ttl_expires_yields_no_fill(
    venue_config, venue_session, reference_session, feed, instrument
) -> None:
    ts = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    rfq = make_rfq(ts)
    outcome = _process(rfq, venue_config, venue_session, reference_session, feed, instrument)
    live_quote = outcome.live_quote
    assert live_quote is not None
    late_ts = live_quote.quote.ttl_expiry_ts + timedelta(seconds=1)
    fill = accept_quote(
        live_quote,
        late_ts,
        500.0,
        Side.BUY,
        "req-00001",
        feed=feed,
        venue_config=venue_config,
        is_informed=False,
        informed_drift_bps=15.0,
        markout_tracker=MarkoutTracker(),
        compliance_log=ComplianceLog.empty(),
    )
    assert fill is None
    assert live_quote.status == QuoteStatus.EXPIRED


def test_accept_withdrawn_when_market_moves_materially(
    venue_config, venue_session, reference_session, feed, instrument
) -> None:
    ts = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    rfq = make_rfq(ts)
    outcome = _process(rfq, venue_config, venue_session, reference_session, feed, instrument)
    live_quote = outcome.live_quote
    assert live_quote is not None

    feed.apply_informed_drift(ISIN, ts + timedelta(seconds=1), drift_bps=500.0)
    log = ComplianceLog.empty()
    fill = accept_quote(
        live_quote,
        ts + timedelta(seconds=2),
        500.0,
        Side.BUY,
        "req-00001",
        feed=feed,
        venue_config=venue_config,
        is_informed=False,
        informed_drift_bps=15.0,
        markout_tracker=MarkoutTracker(),
        compliance_log=log,
    )
    assert fill is None
    assert live_quote.status == QuoteStatus.WITHDRAWN
    assert len(log.withdrawal_events) == 1


def test_firm_size_breach_blocks_further_fills_and_triggers_halt(venue_config, feed) -> None:
    ts = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    current = feed.bbo(ts, ISIN, 1_000.0)
    quote = Quote(
        quote_id="q-test",
        rfq_id="rfq-test",
        ts_issued=ts,
        isin=ISIN,
        bid=current.bid,
        ask=current.ask,
        firm_size_eur=1_000.0,
        ttl_expiry_ts=ts + timedelta(seconds=10),
        is_voluntary=False,
    )
    live_quote = LiveQuote(quote=quote, remaining_firm_size_eur=1_000.0, status=QuoteStatus.OPEN)
    log = ComplianceLog.empty()

    fill1 = accept_quote(
        live_quote,
        ts + timedelta(seconds=1),
        700.0,
        Side.BUY,
        "req-1",
        feed=feed,
        venue_config=venue_config,
        is_informed=False,
        informed_drift_bps=0.0,
        markout_tracker=MarkoutTracker(),
        compliance_log=log,
    )
    assert fill1 is not None
    assert live_quote.remaining_firm_size_eur == 300.0
    assert live_quote.status == QuoteStatus.OPEN

    fill2 = accept_quote(
        live_quote,
        ts + timedelta(seconds=2),
        500.0,
        Side.BUY,
        "req-2",
        feed=feed,
        venue_config=venue_config,
        is_informed=False,
        informed_drift_bps=0.0,
        markout_tracker=MarkoutTracker(),
        compliance_log=log,
    )
    assert fill2 is None
    assert live_quote.status == QuoteStatus.WITHDRAWN
    assert len(log.breach_events) == 1
    expected_halt = (
        ts
        + timedelta(seconds=2)
        + timedelta(seconds=venue_config.quote.firm_size_breach_halt_seconds)
    )
    assert log.breach_events[0].halt_until == expected_halt

    fill3 = accept_quote(
        live_quote,
        ts + timedelta(seconds=3),
        100.0,
        Side.BUY,
        "req-3",
        feed=feed,
        venue_config=venue_config,
        is_informed=False,
        informed_drift_bps=0.0,
        markout_tracker=MarkoutTracker(),
        compliance_log=log,
    )
    assert fill3 is None  # quote already withdrawn, no further fills


def test_informed_fill_injects_drift_into_future_prices(venue_config, feed) -> None:
    ts = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    before = feed.bbo(ts + timedelta(seconds=30), ISIN, 1_000.0).mid
    current = feed.bbo(ts, ISIN, 1_000.0)
    quote = Quote(
        quote_id="q-informed",
        rfq_id="rfq-informed",
        ts_issued=ts,
        isin=ISIN,
        bid=current.bid,
        ask=current.ask,
        firm_size_eur=1_000.0,
        ttl_expiry_ts=ts + timedelta(seconds=10),
        is_voluntary=False,
    )
    live_quote = LiveQuote(quote=quote, remaining_firm_size_eur=1_000.0, status=QuoteStatus.OPEN)
    fill = accept_quote(
        live_quote,
        ts + timedelta(seconds=1),
        500.0,
        Side.BUY,
        "req-1",
        feed=feed,
        venue_config=venue_config,
        is_informed=True,
        informed_drift_bps=500.0,
        markout_tracker=MarkoutTracker(),
        compliance_log=ComplianceLog.empty(),
    )
    assert fill is not None
    after = feed.bbo(ts + timedelta(seconds=30), ISIN, 1_000.0).mid
    assert after > before  # BUY-side informed fill drifts price up


def test_decide_acceptance_respects_probability_and_ttl() -> None:
    ts = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    quote = Quote(
        quote_id="q1",
        rfq_id="r1",
        ts_issued=ts,
        isin=ISIN,
        bid=100.0,
        ask=101.0,
        firm_size_eur=1_000.0,
        ttl_expiry_ts=ts + timedelta(seconds=10),
        is_voluntary=False,
    )
    accepted = decide_acceptance(
        quote, default_rng(1), p_accept=1.0, reaction_delay_range=(0.5, 8.0)
    )
    assert accepted is not None
    assert ts < accepted <= quote.ttl_expiry_ts

    declined = decide_acceptance(quote, default_rng(2), p_accept=0.0)
    assert declined is None
