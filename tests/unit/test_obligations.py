from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.types import RFQ, Side
from rfq_mm.feed.synthetic import SyntheticFeed
from rfq_mm.venue.config import (
    ConformityCheckExemption,
    CorporateActionCumDayExemption,
    ObligationsConfig,
    ReferenceHaltExemption,
    VenueConfig,
)
from rfq_mm.venue.obligations import ExemptionContext, ResponseRatioTracker, evaluate_exemption

TZ = ZoneInfo("Europe/Berlin")
ISIN = "DE0007164600"


def _rfq(ts: datetime, requester_id: str = "req-00001") -> RFQ:
    return RFQ(
        rfq_id=f"rfq-{ts.isoformat()}",
        ts=ts,
        isin=ISIN,
        requester_id=requester_id,
        side=Side.BUY,
        requested_size_eur=1_000.0,
        is_exempt=False,
    )


def test_after_venue_open_exemption(
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: SyntheticFeed,
) -> None:
    ctx = ExemptionContext(
        venue_session=venue_session, reference_session=reference_session, feed=feed
    )
    rfq = _rfq(datetime(2026, 9, 7, 8, 5, tzinfo=TZ))
    result = evaluate_exemption(rfq, venue_config.obligations, ctx)
    assert result.is_exempt
    assert result.exemption_type == "after_venue_open"


def test_around_reference_open_exemption(
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: SyntheticFeed,
) -> None:
    ctx = ExemptionContext(
        venue_session=venue_session, reference_session=reference_session, feed=feed
    )
    rfq = _rfq(datetime(2026, 9, 7, 8, 55, tzinfo=TZ))
    result = evaluate_exemption(rfq, venue_config.obligations, ctx)
    assert result.is_exempt
    assert result.exemption_type == "around_reference_open"


def test_reference_indicative_only_before_open(
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: SyntheticFeed,
) -> None:
    ctx = ExemptionContext(
        venue_session=venue_session, reference_session=reference_session, feed=feed
    )
    rfq = _rfq(datetime(2026, 9, 7, 8, 30, tzinfo=TZ))
    result = evaluate_exemption(rfq, venue_config.obligations, ctx)
    assert result.is_exempt
    assert result.exemption_type == "reference_indicative_only"


def test_reference_indicative_only_after_close(
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: SyntheticFeed,
) -> None:
    ctx = ExemptionContext(
        venue_session=venue_session, reference_session=reference_session, feed=feed
    )
    rfq = _rfq(datetime(2026, 9, 7, 18, 0, tzinfo=TZ))
    result = evaluate_exemption(rfq, venue_config.obligations, ctx)
    assert result.is_exempt
    assert result.exemption_type == "reference_indicative_only"


def test_no_exemption_at_neutral_time(
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: SyntheticFeed,
) -> None:
    ctx = ExemptionContext(
        venue_session=venue_session, reference_session=reference_session, feed=feed
    )
    rfq = _rfq(datetime(2026, 9, 7, 13, 0, tzinfo=TZ))
    result = evaluate_exemption(rfq, venue_config.obligations, ctx)
    assert not result.is_exempt
    assert result.exemption_type is None


def test_extreme_move_exemption_triggers_on_injected_jump(
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: SyntheticFeed,
) -> None:
    jump_ts = datetime(2026, 9, 7, 12, 0, 0, tzinfo=TZ)
    feed.apply_informed_drift(ISIN, jump_ts, drift_bps=500.0)

    ctx = ExemptionContext(
        venue_session=venue_session, reference_session=reference_session, feed=feed
    )
    rfq = _rfq(jump_ts.replace(second=30))
    result = evaluate_exemption(rfq, venue_config.obligations, ctx)
    assert result.is_exempt
    assert result.exemption_type == "extreme_move"


def test_mm_system_outage_exemption(
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: SyntheticFeed,
) -> None:
    ts = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    ctx = ExemptionContext(
        venue_session=venue_session,
        reference_session=reference_session,
        feed=feed,
        outage_windows=((ts - timedelta(minutes=5), ts + timedelta(minutes=5)),),
    )
    rfq = _rfq(ts)
    result = evaluate_exemption(rfq, venue_config.obligations, ctx)
    assert result.is_exempt
    assert result.exemption_type == "mm_system_outage"


def test_suspected_manipulation_exemption(
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: SyntheticFeed,
) -> None:
    ts = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    ctx = ExemptionContext(
        venue_session=venue_session,
        reference_session=reference_session,
        feed=feed,
        flagged_manipulation_requesters=frozenset({"req-00001"}),
    )
    rfq = _rfq(ts, requester_id="req-00001")
    result = evaluate_exemption(rfq, venue_config.obligations, ctx)
    assert result.is_exempt
    assert result.exemption_type == "suspected_manipulation"


def test_never_triggered_exemption_types_never_match(
    venue_session: SessionCalendar, reference_session: SessionCalendar, feed: SyntheticFeed
) -> None:
    config = ObligationsConfig(
        min_response_ratio=0.95,
        exemptions=[
            ReferenceHaltExemption(type="reference_halt", grace_minutes=3),
            CorporateActionCumDayExemption(type="corporate_action_cum_day", after="19:55"),
            ConformityCheckExemption(type="conformity_check", max_seconds=30),
        ],
    )
    ctx = ExemptionContext(
        venue_session=venue_session, reference_session=reference_session, feed=feed
    )
    for hour in (8, 10, 13, 18, 21):
        rfq = _rfq(datetime(2026, 9, 7, hour, 0, tzinfo=TZ))
        result = evaluate_exemption(rfq, config, ctx)
        assert not result.is_exempt


def test_response_ratio_tracker_excludes_exempt_and_tracks_ratio() -> None:
    tracker = ResponseRatioTracker()
    tracker.record("A", is_exempt=True, was_answered=False)  # excluded entirely
    tracker.record("A", is_exempt=False, was_answered=True)
    tracker.record("A", is_exempt=False, was_answered=True)
    tracker.record("A", is_exempt=False, was_answered=False)
    tracker.record("B", is_exempt=False, was_answered=True)

    assert tracker.presented == 4
    assert tracker.answered == 3
    assert tracker.ratio == 0.75
    assert tracker.ratio_for_isin("A") == 2 / 3
    assert tracker.ratio_for_isin("B") == 1.0
    assert tracker.is_breaching(0.95)
    assert not tracker.is_breaching(0.5)


def test_response_ratio_tracker_empty_is_not_breaching() -> None:
    tracker = ResponseRatioTracker()
    assert tracker.ratio == 1.0
    assert not tracker.is_breaching(0.95)
