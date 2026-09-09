"""Aggregates response ratio, clamp events, withdrawals, and firm-size
breaches into one compliance report with breach flags.

The withdrawal-rate threshold is a documented assumption (spec.md tracks
withdrawal rate as a metric, R156, but doesn't set a compliance
threshold for it) — same honesty caveat as engine.quoter.TradingCosts.
"""

from __future__ import annotations

from dataclasses import dataclass

from rfq_mm.engine.rfq import ComplianceLog
from rfq_mm.venue.obligations import ResponseRatioTracker


@dataclass(frozen=True, slots=True)
class ComplianceBreach:
    category: str
    detail: str


@dataclass(frozen=True, slots=True)
class ComplianceReport:
    response_ratio: float
    response_ratio_floor: float
    presented: int
    answered: int
    n_clamp_events: int
    n_withdrawals: int
    n_firm_size_breaches: int
    withdrawal_rate: float
    breaches: tuple[ComplianceBreach, ...]

    @property
    def is_compliant(self) -> bool:
        return len(self.breaches) == 0


def build_compliance_report(
    response_tracker: ResponseRatioTracker,
    compliance_log: ComplianceLog,
    n_accept_attempts: int,
    min_response_ratio: float,
    max_withdrawal_rate: float = 0.10,
) -> ComplianceReport:
    breaches: list[ComplianceBreach] = []

    if response_tracker.is_breaching(min_response_ratio):
        breaches.append(
            ComplianceBreach(
                "response_ratio",
                f"ratio {response_tracker.ratio:.4f} below floor {min_response_ratio}",
            )
        )

    withdrawal_rate = (
        len(compliance_log.withdrawal_events) / n_accept_attempts if n_accept_attempts else 0.0
    )
    if withdrawal_rate > max_withdrawal_rate:
        breaches.append(
            ComplianceBreach(
                "withdrawal_rate",
                f"rate {withdrawal_rate:.4f} above threshold {max_withdrawal_rate}",
            )
        )

    if compliance_log.breach_events:
        breaches.append(
            ComplianceBreach(
                "firm_size_breach",
                f"{len(compliance_log.breach_events)} firm-size breach(es) (§8(3))",
            )
        )

    return ComplianceReport(
        response_ratio=response_tracker.ratio,
        response_ratio_floor=min_response_ratio,
        presented=response_tracker.presented,
        answered=response_tracker.answered,
        n_clamp_events=len(compliance_log.clamp_events),
        n_withdrawals=len(compliance_log.withdrawal_events),
        n_firm_size_breaches=len(compliance_log.breach_events),
        withdrawal_rate=withdrawal_rate,
        breaches=tuple(breaches),
    )
