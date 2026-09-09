import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from rfq_mm.compliance.audit import AuditLog

TS = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def test_record_appends_to_memory_and_jsonl_file(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "audit.jsonl"
    log = AuditLog(jsonl_path)
    log.record(TS, "fill", "DE0007164600", {"price": 220.5, "size_eur": 500.0})
    log.record(TS, "withdrawal", "DE0007164600", {"quote_id": "q1"})
    log.close()

    assert len(log.records) == 2
    lines = jsonl_path.read_text().strip().splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row["event"] == "fill"
    assert row["isin"] == "DE0007164600"
    assert row["price"] == 220.5


def test_record_is_append_only_across_instances(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "audit.jsonl"
    log1 = AuditLog(jsonl_path)
    log1.record(TS, "fill", "X", {})
    log1.close()

    log2 = AuditLog(jsonl_path)
    log2.record(TS, "fill", "X", {})
    log2.close()

    lines = jsonl_path.read_text().strip().splitlines()
    assert len(lines) == 2  # second instance appended, did not truncate


def test_fx_fields_recorded_for_foreign_instrument(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "audit.jsonl"
    log = AuditLog(jsonl_path)
    log.record(
        TS,
        "fill",
        "SYN-DJ30-01",
        {"price": 150.0},
        fx_rate=0.92,
        fx_conversion_formula="price_eur = price_usd * fx_rate",
    )
    log.close()
    row = json.loads(jsonl_path.read_text().strip())
    assert row["fx_rate"] == 0.92
    assert row["fx_conversion_formula"] == "price_eur = price_usd * fx_rate"


def test_to_parquet_round_trips(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "audit.jsonl"
    parquet_path = tmp_path / "audit.parquet"
    log = AuditLog(jsonl_path)
    for i in range(5):
        log.record(TS, "fill", "X", {"i": i})
    log.to_parquet(parquet_path)
    log.close()

    df = pd.read_parquet(parquet_path)
    assert len(df) == 5
    assert set(df["event_type"]) == {"fill"}
