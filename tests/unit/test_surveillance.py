from datetime import UTC, datetime, timedelta

from rfq_mm.compliance.surveillance import (
    detect_high_cancel_ratio,
    detect_high_frequency_requesters,
)
from rfq_mm.core.types import RFQ, Side

TS = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def make_rfq(rfq_id: str, ts: datetime, requester_id: str) -> RFQ:
    return RFQ(
        rfq_id=rfq_id,
        ts=ts,
        isin="X",
        requester_id=requester_id,
        side=Side.BUY,
        requested_size_eur=100.0,
        is_exempt=False,
    )


def test_high_frequency_requester_flagged() -> None:
    rfqs = [
        make_rfq(f"r{i}", TS + timedelta(seconds=i * 2), "abuser") for i in range(40)
    ]  # 40 requests inside 1 minute
    flags = detect_high_frequency_requesters(rfqs, max_per_minute=30)
    assert len(flags) == 1
    assert flags[0].requester_id == "abuser"
    assert flags[0].category == "high_frequency"


def test_normal_requester_not_flagged() -> None:
    rfqs = [make_rfq(f"r{i}", TS + timedelta(minutes=i * 5), "normal") for i in range(10)]
    flags = detect_high_frequency_requesters(rfqs, max_per_minute=30)
    assert flags == []


def test_high_cancel_ratio_flagged() -> None:
    rfqs = [make_rfq(f"r{i}", TS + timedelta(minutes=i), "canceler") for i in range(10)]
    filled_ids = {"r0", "r1"}  # only 2 of 10 filled -> 80% unfilled
    flags = detect_high_cancel_ratio(rfqs, filled_ids, max_cancel_ratio=0.5)
    assert len(flags) == 1
    assert flags[0].requester_id == "canceler"


def test_low_volume_requester_not_flagged_even_if_all_unfilled() -> None:
    rfqs = [make_rfq(f"r{i}", TS + timedelta(minutes=i), "quiet") for i in range(3)]
    flags = detect_high_cancel_ratio(
        rfqs, filled_rfq_ids=set(), max_cancel_ratio=0.5, min_requests=5
    )
    assert flags == []


def test_healthy_requester_not_flagged() -> None:
    rfqs = [make_rfq(f"r{i}", TS + timedelta(minutes=i), "healthy") for i in range(10)]
    filled_ids = {f"r{i}" for i in range(9)}  # 90% filled
    flags = detect_high_cancel_ratio(rfqs, filled_ids, max_cancel_ratio=0.5)
    assert flags == []
