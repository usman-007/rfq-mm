"""write_audit_trail (engine/simulation.py) had no test at all before this
file: only its building block, compliance.audit.AuditLog, was covered
(see test_audit.py). R25/R26 (§15) require every material event of the
day to reach the audit log -- this exercises that mapping directly."""

from datetime import UTC, datetime
from pathlib import Path

from rfq_mm.compliance.audit import AuditLog
from rfq_mm.core.types import Fill, Instrument, Segment, Side
from rfq_mm.engine.rfq import (
    ClampEvent,
    ComplianceLog,
    FirmSizeBreachEvent,
    HaltBlockedEvent,
    WithdrawalEvent,
)
from rfq_mm.engine.simulation import SimulationResult, write_audit_trail
from rfq_mm.venue.obligations import ResponseRatioTracker

TS = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
ISIN = "DE0007164600"
FOREIGN_ISIN = "SYN-DJ30-01"


def _result(fills: list[Fill], log: ComplianceLog) -> SimulationResult:
    return SimulationResult(
        sim_date=TS.date(),
        rfqs=[],
        quotes=[],
        fills=fills,
        filled_quote_ids=set(),
        hedges=[],
        outcomes=[],
        compliance_log=log,
        response_tracker=ResponseRatioTracker(),
        inventory_snapshots={},
        final_units={},
        final_mark={},
        killed_isins=set(),
    )


def test_write_audit_trail_records_every_event_category(tmp_path: Path) -> None:
    fill = Fill(
        fill_id="f1",
        quote_id="q1",
        ts=TS,
        isin=ISIN,
        side=Side.BUY,
        price=100.0,
        size_eur=1_000.0,
        requester_id="r1",
        is_informed_label=False,
    )
    log = ComplianceLog.empty()
    log.withdrawal_events.append(WithdrawalEvent(ts=TS, isin=ISIN, quote_id="q2"))
    log.breach_events.append(
        FirmSizeBreachEvent(ts=TS, isin=ISIN, quote_id="q3", halt_until=TS)
    )
    log.halted_events.append(
        HaltBlockedEvent(ts=TS, isin=ISIN, rfq_id="rfq-4", halt_until=TS)
    )
    log.clamp_events.append(ClampEvent(ts=TS, isin=ISIN, risk_delta=0.5, regulatory_cap=0.3))

    result = _result([fill], log)
    instrument = Instrument(
        isin=ISIN, name="Test", segment=Segment.DAX, currency="EUR", liquidity_band=6
    )

    audit_log = AuditLog(tmp_path / "audit.jsonl")
    write_audit_trail(result, {ISIN: instrument}, audit_log)
    audit_log.close()

    event_types = [r.event_type for r in audit_log.records]
    assert event_types == [
        "fill",
        "withdrawal",
        "firm_size_breach",
        "halt_blocked_rfq",
        "clamp_bound",
    ]

    halt_record = next(r for r in audit_log.records if r.event_type == "halt_blocked_rfq")
    assert halt_record.isin == ISIN
    assert halt_record.payload["rfq_id"] == "rfq-4"
    assert halt_record.payload["halt_until"] == TS.isoformat()


def test_write_audit_trail_records_fx_rate_for_foreign_instrument(tmp_path: Path) -> None:
    fill = Fill(
        fill_id="f1",
        quote_id="q1",
        ts=TS,
        isin=FOREIGN_ISIN,
        side=Side.BUY,
        price=100.0,
        size_eur=1_000.0,
        requester_id="r1",
        is_informed_label=False,
    )
    result = _result([fill], ComplianceLog.empty())
    instrument = Instrument(
        isin=FOREIGN_ISIN,
        name="Foreign",
        segment=Segment.DJ30,
        currency="USD",
        liquidity_band=6,
        fx_rate_to_eur=0.92,
    )

    audit_log = AuditLog(tmp_path / "audit.jsonl")
    write_audit_trail(result, {FOREIGN_ISIN: instrument}, audit_log)
    audit_log.close()

    fill_record = audit_log.records[0]
    assert fill_record.fx_rate == 0.92
    assert fill_record.fx_conversion_formula == "price_eur = price_local * fx_rate"
