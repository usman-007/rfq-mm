"""§15 immutable audit trail: one append-only JSONL row per material event
(quote issued, fill, withdrawal, firm-size breach, clamp-bound, exemption
invoked), via structlog — doubling as the audit trail per spec.md §1.
FX rate and conversion formula are recorded for any non-EUR instrument.

`mm audit export` (§16) reads the JSONL back and/or exports it to Parquet
via to_parquet(); see cli.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog


@dataclass(frozen=True, slots=True)
class AuditRecord:
    ts: datetime
    event_type: str
    isin: str
    payload: dict[str, Any]
    fx_rate: float | None = None
    fx_conversion_formula: str | None = None


class AuditLog:
    """Append-only: `record()` only ever appends, both to the in-memory
    list (used by to_parquet()) and to the JSONL file on disk (opened in
    append mode, never truncated)."""

    def __init__(self, jsonl_path: Path) -> None:
        self._jsonl_path = jsonl_path
        self._file = jsonl_path.open("a", encoding="utf-8")
        self._logger = structlog.wrap_logger(
            structlog.PrintLogger(file=self._file),
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(),
            ],
        )
        self._records: list[AuditRecord] = []

    def record(
        self,
        ts: datetime,
        event_type: str,
        isin: str,
        payload: dict[str, Any],
        *,
        fx_rate: float | None = None,
        fx_conversion_formula: str | None = None,
    ) -> None:
        rec = AuditRecord(
            ts=ts,
            event_type=event_type,
            isin=isin,
            payload=payload,
            fx_rate=fx_rate,
            fx_conversion_formula=fx_conversion_formula,
        )
        self._records.append(rec)
        self._logger.info(
            event_type,
            ts=ts.isoformat(),
            isin=isin,
            fx_rate=fx_rate,
            fx_conversion_formula=fx_conversion_formula,
            **payload,
        )

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def close(self) -> None:
        self._file.close()

    def to_parquet(self, path: Path) -> None:
        import pandas as pd

        rows = [
            {
                "ts": r.ts,
                "event_type": r.event_type,
                "isin": r.isin,
                "fx_rate": r.fx_rate,
                "fx_conversion_formula": r.fx_conversion_formula,
                "payload_json": json.dumps(r.payload, default=str),
            }
            for r in self._records
        ]
        pd.DataFrame(rows).to_parquet(path)
