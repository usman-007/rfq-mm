"""One-day simulation orchestrator: strings flow.generator, engine.rfq,
risk.inventory, risk.hedger, compliance, and analytics.metrics together.

Not a file the spec.md repo tree names explicitly, but necessary glue
between the modules it does name — M5's done-when ("end-to-end sim
produces fills and a P&L series") and the CI's "run a 1-day sim" both
need somewhere to live. Kept in engine/ since it's simulating one trading
day, the engine layer's job.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from numpy.random import default_rng

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.types import RFQ, Fill, Instrument, Quote
from rfq_mm.engine.quoter import MarkoutTracker, QuoterParams, TradingCosts
from rfq_mm.engine.rfq import (
    ComplianceLog,
    RfqOutcome,
    accept_quote,
    decide_acceptance,
    process_rfq,
)
from rfq_mm.feed.base import ReferenceMarketFeed
from rfq_mm.flow.generator import FlowParams, generate_rfqs
from rfq_mm.risk.hedger import HedgeParams, HedgeResult, maybe_hedge
from rfq_mm.risk.inventory import InventoryBook, InventoryLimits
from rfq_mm.venue.config import VenueConfig
from rfq_mm.venue.obligations import ResponseRatioTracker


@dataclass(frozen=True, slots=True)
class SimulationParams:
    seed: int
    sim_date: date
    isins: list[str]
    quoter_params: QuoterParams = field(default_factory=QuoterParams)
    costs: TradingCosts = field(default_factory=TradingCosts)
    flow_params_overrides: dict[str, float] | None = None
    hedge_params: HedgeParams = field(default_factory=HedgeParams)
    inventory_limits: InventoryLimits = field(default_factory=InventoryLimits)
    p_accept: float = 0.9
    reaction_delay_range: tuple[float, float] = (0.5, 8.0)
    markout_feedback_horizon_seconds: float = 10.0


@dataclass(slots=True)
class SimulationResult:
    sim_date: date
    rfqs: list[RFQ]
    quotes: list[Quote]
    fills: list[Fill]
    filled_quote_ids: set[str]
    hedges: list[HedgeResult]
    outcomes: list[RfqOutcome]
    compliance_log: ComplianceLog
    response_tracker: ResponseRatioTracker
    inventory_snapshots: dict[str, list[tuple[datetime, float]]]
    final_units: dict[str, float]
    final_mark: dict[str, float]
    killed_isins: set[str]


def run_day_simulation(
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: ReferenceMarketFeed,
    instruments: dict[str, Instrument],
    params: SimulationParams,
) -> SimulationResult:
    flow_params = FlowParams(seed=params.seed)
    if params.flow_params_overrides:
        # dataclasses.replace can't be statically checked against **a dict of
        # mixed-type overrides; FlowParams' own __post_init__-free field
        # types are enforced at runtime, which is what matters here.
        flow_params = dataclasses.replace(
            flow_params,
            **params.flow_params_overrides,  # type: ignore[arg-type]
        )
    flow = generate_rfqs(venue_session, params.sim_date, params.isins, flow_params)

    rng = default_rng([params.seed, 0xACCE97])  # distinct salt from the flow/price RNG streams
    markout_tracker = MarkoutTracker()
    response_tracker = ResponseRatioTracker()
    compliance_log = ComplianceLog.empty()
    book = InventoryBook(limits=params.inventory_limits)

    quotes: list[Quote] = []
    fills: list[Fill] = []
    filled_quote_ids: set[str] = set()
    hedges: list[HedgeResult] = []
    outcomes: list[RfqOutcome] = []
    inventory_snapshots: dict[str, list[tuple[datetime, float]]] = {
        isin: [] for isin in params.isins
    }
    killed_isins: set[str] = set()
    final_mark: dict[str, float] = {}

    day_start = venue_session.session_open(params.sim_date)
    for isin in params.isins:
        inventory_snapshots[isin].append((day_start, 0.0))

    for rfq in flow.rfqs:
        instrument = instruments[rfq.isin]
        mark = feed.bbo(rfq.ts, rfq.isin, rfq.requested_size_eur).mid
        final_mark[rfq.isin] = mark

        if rfq.isin in killed_isins:
            outcomes.append(RfqOutcome(rfq=rfq, quote=None, live_quote=None))
            continue

        outcome = process_rfq(
            rfq,
            venue_config=venue_config,
            venue_session=venue_session,
            reference_session=reference_session,
            feed=feed,
            instrument=instrument,
            inventory_units=book.units(rfq.isin),
            inventory_headroom_eur=book.headroom_eur(rfq.isin, mark),
            quoter_params=params.quoter_params,
            costs=params.costs,
            markout_tracker=markout_tracker,
            response_tracker=response_tracker,
            compliance_log=compliance_log,
        )
        outcomes.append(outcome)
        if outcome.quote is None or outcome.live_quote is None:
            continue
        quotes.append(outcome.quote)

        accept_ts = decide_acceptance(
            outcome.quote,
            rng,
            p_accept=params.p_accept,
            reaction_delay_range=params.reaction_delay_range,
        )
        if accept_ts is None:
            continue

        accept_size = min(rfq.requested_size_eur, outcome.quote.firm_size_eur)
        fill = accept_quote(
            outcome.live_quote,
            accept_ts,
            accept_size,
            rfq.side,
            rfq.requester_id,
            feed=feed,
            venue_config=venue_config,
            is_informed=rfq.is_informed,
            informed_drift_bps=flow_params.informed_drift_bps,
            markout_tracker=markout_tracker,
            compliance_log=compliance_log,
            markout_feedback_horizon_seconds=params.markout_feedback_horizon_seconds,
        )
        if fill is None:
            continue

        fills.append(fill)
        filled_quote_ids.add(fill.quote_id)
        book.record_fill(fill)
        mark = feed.bbo(fill.ts, fill.isin, fill.size_eur).mid
        final_mark[fill.isin] = mark
        inventory_snapshots[fill.isin].append((fill.ts, book.position_eur(fill.isin, mark)))

        if book.is_killed(fill.isin, mark):
            killed_isins.add(fill.isin)

        hedge_bbo = feed.bbo(fill.ts, fill.isin, accept_size)
        hedge = maybe_hedge(
            fill.isin, book.units(fill.isin), hedge_bbo, fill.ts, params.hedge_params
        )
        if hedge is not None:
            book.apply_hedge(fill.isin, hedge.side.sign, hedge.units, hedge.price, hedge.ts)
            hedges.append(hedge)
            mark = feed.bbo(hedge.ts, hedge.isin, hedge.units * hedge.price).mid
            final_mark[hedge.isin] = mark
            inventory_snapshots[hedge.isin].append((hedge.ts, book.position_eur(hedge.isin, mark)))

    day_end = venue_session.session_close(params.sim_date) - timedelta(seconds=1)
    for isin in params.isins:
        closing_mark = final_mark.get(isin)
        if closing_mark is None:
            try:
                closing_mark = feed.bbo(day_end, isin, 1_000.0).mid
            except ValueError:
                closing_mark = 0.0
            final_mark[isin] = closing_mark
        inventory_snapshots[isin].append((day_end, book.position_eur(isin, closing_mark)))

    return SimulationResult(
        sim_date=params.sim_date,
        rfqs=flow.rfqs,
        quotes=quotes,
        fills=fills,
        filled_quote_ids=filled_quote_ids,
        hedges=hedges,
        outcomes=outcomes,
        compliance_log=compliance_log,
        response_tracker=response_tracker,
        inventory_snapshots=inventory_snapshots,
        final_units={isin: book.units(isin) for isin in params.isins},
        final_mark=final_mark,
        killed_isins=killed_isins,
    )
