from datetime import UTC, datetime

from rfq_mm.compliance.monitor import build_compliance_report
from rfq_mm.engine.rfq import ComplianceLog, FirmSizeBreachEvent, HaltBlockedEvent, WithdrawalEvent
from rfq_mm.venue.obligations import ResponseRatioTracker

TS = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def test_benign_scenario_is_compliant() -> None:
    tracker = ResponseRatioTracker()
    for _ in range(100):
        tracker.record("X", is_exempt=False, was_answered=True)
    log = ComplianceLog.empty()

    report = build_compliance_report(tracker, log, n_accept_attempts=100, min_response_ratio=0.95)
    assert report.is_compliant
    assert report.breaches == ()
    assert report.response_ratio == 1.0


def test_stressed_scenario_flags_response_ratio_breach() -> None:
    tracker = ResponseRatioTracker()
    for _ in range(50):
        tracker.record("X", is_exempt=False, was_answered=True)
    for _ in range(50):
        tracker.record("X", is_exempt=False, was_answered=False)
    log = ComplianceLog.empty()

    report = build_compliance_report(tracker, log, n_accept_attempts=50, min_response_ratio=0.95)
    assert not report.is_compliant
    assert any(b.category == "response_ratio" for b in report.breaches)


def test_stressed_scenario_flags_withdrawal_rate_and_firm_size_breach() -> None:
    tracker = ResponseRatioTracker()
    for _ in range(20):
        tracker.record("X", is_exempt=False, was_answered=True)
    log = ComplianceLog.empty()
    for _ in range(5):
        log.withdrawal_events.append(WithdrawalEvent(ts=TS, isin="X", quote_id="q1"))
    log.breach_events.append(FirmSizeBreachEvent(ts=TS, isin="X", quote_id="q1", halt_until=TS))

    report = build_compliance_report(
        tracker, log, n_accept_attempts=20, min_response_ratio=0.95, max_withdrawal_rate=0.10
    )
    assert not report.is_compliant
    categories = {b.category for b in report.breaches}
    assert "withdrawal_rate" in categories
    assert "firm_size_breach" in categories


def test_halted_rfqs_are_counted_but_not_a_breach_on_their_own() -> None:
    """R15: a §8(3) halt correctly blocking quoting is the system working as
    intended, not a compliance failure — it's surfaced as a count for
    visibility, same as n_clamp_events, without adding a breach entry."""
    tracker = ResponseRatioTracker()
    for _ in range(20):
        tracker.record("X", is_exempt=False, was_answered=True)
    log = ComplianceLog.empty()
    for _ in range(3):
        log.halted_events.append(
            HaltBlockedEvent(ts=TS, isin="X", rfq_id="rfq-1", halt_until=TS)
        )

    report = build_compliance_report(tracker, log, n_accept_attempts=20, min_response_ratio=0.95)
    assert report.is_compliant
    assert report.n_halted_rfqs == 3
