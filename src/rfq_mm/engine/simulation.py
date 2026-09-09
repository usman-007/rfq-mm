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

from numpy.random import Generator, default_rng

from rfq_mm.compliance.audit import AuditLog
from rfq_mm.core.clock import SessionCalendar
from rfq_mm.core.types import RFQ, Fill, Instrument, Quote
from rfq_mm.engine.lom import (
    LimitOrder,
    LimitOrderBook,
    LomFlowParams,
    generate_limit_orders,
    recheck_tick_times,
)
from rfq_mm.engine.quoter import MarkoutTracker, QuoterParams, TradingCosts
from rfq_mm.engine.rfq import (
    ComplianceLog,
    HaltBlockedEvent,
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


def _active_halt(
    active_halts: dict[str, datetime], isin: str, ts: datetime
) -> datetime | None:
    """§8(3): returns the still-active halt_until for `isin` as of `ts`, or
    None if it isn't halted (expiring + removing a stale entry as a side
    effect, so a caller never has to separately clean the dict up).

    Honesty note: the firm-size breach that triggers a halt requires
    cumulative fills against *one* quote to exceed its firm size (§8(3)),
    and this simulator issues a fresh quote per RFQ with only that RFQ's
    requester able to accept it (Quote:Fill is 1:many in the data model,
    but nothing in this loop ever produces more than one accept_quote()
    call per quote) — so a breach, and therefore a halt, cannot actually
    occur via run_day_simulation as currently architected. The mechanism
    itself (accept_quote's remaining_firm_size_eur budget, and the halt
    gating here) is real and directly unit-tested at both layers; it's
    exercised end-to-end only if a future caller starts routing multiple
    acceptances at the same quote_id."""
    halt_until = active_halts.get(isin)
    if halt_until is None:
        return None
    if ts < halt_until:
        return halt_until
    del active_halts[isin]
    return None


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
    lom_params: LomFlowParams | None = None  # §9; None => LomFlowParams(seed=seed)
    lom_recheck_seconds: float = 15.0


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
    lom_orders: list[LimitOrder]


@dataclass(slots=True)
class _LoopState:
    """Mutable state shared across every RFQ processed in a simulated day —
    whether it arrived from the client flow or was auto-fired by the LOM
    (engine/lom.py) — so both paths record fills/quotes/inventory/hedges
    through the exact same code (_process_rfq below)."""

    rng: Generator
    markout_tracker: MarkoutTracker
    response_tracker: ResponseRatioTracker
    compliance_log: ComplianceLog
    book: InventoryBook
    quotes: list[Quote]
    fills: list[Fill]
    filled_quote_ids: set[str]
    hedges: list[HedgeResult]
    outcomes: list[RfqOutcome]
    inventory_snapshots: dict[str, list[tuple[datetime, float]]]
    killed_isins: set[str]
    active_halts: dict[str, datetime]
    final_mark: dict[str, float]


def _process_rfq(
    rfq: RFQ,
    *,
    forced_accept_ts: datetime | None,
    venue_config: VenueConfig,
    venue_session: SessionCalendar,
    reference_session: SessionCalendar,
    feed: ReferenceMarketFeed,
    instruments: dict[str, Instrument],
    flow_params: FlowParams,
    sim_params: SimulationParams,
    state: _LoopState,
) -> Fill | None:
    """Runs one RFQ through the full §5/§8 pipeline (exemption check ->
    quote -> acceptance -> fill) and records its effect on every piece of
    shared day state. Shared by genuine RFQ arrivals (forced_accept_ts is
    None, so acceptance is decide_acceptance's random client-behaviour
    roll) and LOM-triggered auto-fired RFQs (forced_accept_ts is the
    recheck-tick timestamp: a resting limit order is a standing
    commitment, not a fresh ad hoc request to be randomly declined)."""
    instrument = instruments[rfq.isin]
    mark = feed.bbo(rfq.ts, rfq.isin, rfq.requested_size_eur).mid
    state.final_mark[rfq.isin] = mark

    if rfq.isin in state.killed_isins:
        state.outcomes.append(RfqOutcome(rfq=rfq, quote=None, live_quote=None))
        return None

    halt_until = _active_halt(state.active_halts, rfq.isin, rfq.ts)
    if halt_until is not None:
        state.compliance_log.halted_events.append(
            HaltBlockedEvent(ts=rfq.ts, isin=rfq.isin, rfq_id=rfq.rfq_id, halt_until=halt_until)
        )
        state.response_tracker.record(rfq.isin, is_exempt=True, was_answered=False)
        state.outcomes.append(RfqOutcome(rfq=rfq, quote=None, live_quote=None))
        return None

    outcome = process_rfq(
        rfq,
        venue_config=venue_config,
        venue_session=venue_session,
        reference_session=reference_session,
        feed=feed,
        instrument=instrument,
        inventory_units=state.book.units(rfq.isin),
        inventory_headroom_eur=state.book.headroom_eur(rfq.isin, mark),
        quoter_params=sim_params.quoter_params,
        costs=sim_params.costs,
        markout_tracker=state.markout_tracker,
        response_tracker=state.response_tracker,
        compliance_log=state.compliance_log,
    )
    state.outcomes.append(outcome)
    if outcome.quote is None or outcome.live_quote is None:
        return None
    state.quotes.append(outcome.quote)

    if forced_accept_ts is not None:
        accept_ts: datetime | None = forced_accept_ts
    else:
        accept_ts = decide_acceptance(
            outcome.quote,
            state.rng,
            p_accept=sim_params.p_accept,
            reaction_delay_range=sim_params.reaction_delay_range,
        )
    if accept_ts is None:
        return None

    accept_size = min(rfq.requested_size_eur, outcome.quote.firm_size_eur)
    n_breaches_before = len(state.compliance_log.breach_events)
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
        markout_tracker=state.markout_tracker,
        compliance_log=state.compliance_log,
        markout_feedback_horizon_seconds=sim_params.markout_feedback_horizon_seconds,
    )
    if len(state.compliance_log.breach_events) > n_breaches_before:
        state.active_halts[rfq.isin] = state.compliance_log.breach_events[-1].halt_until
    if fill is None:
        return None

    state.fills.append(fill)
    state.filled_quote_ids.add(fill.quote_id)
    state.book.record_fill(fill)
    mark = feed.bbo(fill.ts, fill.isin, fill.size_eur).mid
    state.final_mark[fill.isin] = mark
    state.inventory_snapshots[fill.isin].append(
        (fill.ts, state.book.position_eur(fill.isin, mark))
    )

    if state.book.is_killed(fill.isin, mark):
        state.killed_isins.add(fill.isin)

    hedge_bbo = feed.bbo(fill.ts, fill.isin, accept_size)
    hedge = maybe_hedge(
        fill.isin, state.book.units(fill.isin), hedge_bbo, fill.ts, sim_params.hedge_params
    )
    if hedge is not None:
        state.book.apply_hedge(fill.isin, hedge.side.sign, hedge.units, hedge.price, hedge.ts)
        state.hedges.append(hedge)
        mark = feed.bbo(hedge.ts, hedge.isin, hedge.units * hedge.price).mid
        state.final_mark[hedge.isin] = mark
        state.inventory_snapshots[hedge.isin].append(
            (hedge.ts, state.book.position_eur(hedge.isin, mark))
        )

    return fill


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

    lom_params = params.lom_params or LomFlowParams(seed=params.seed)
    lom_orders = generate_limit_orders(
        venue_session, params.sim_date, params.isins, feed, lom_params
    )
    lom_ticks = recheck_tick_times(venue_session, params.sim_date, params.lom_recheck_seconds)
    lom_book = LimitOrderBook()

    # One time-sorted event stream: RFQ arrivals, LOM order submissions, and
    # LOM price-update recheck ticks (§9's "two execution paths", processed
    # through a single deterministic timeline rather than two separate
    # passes). event kind: 0=rfq, 1=lom_submit, 2=lom_tick; kind breaks ties
    # at an identical timestamp so ordering is deterministic regardless of
    # how the lists above happen to be built.
    events: list[tuple[datetime, int, RFQ | LimitOrder | None]] = []
    events.extend((rfq.ts, 0, rfq) for rfq in flow.rfqs)
    events.extend((order.ts_submitted, 1, order) for order in lom_orders)
    events.extend((ts, 2, None) for ts in lom_ticks)
    events.sort(key=lambda e: (e[0], e[1]))

    state = _LoopState(
        rng=default_rng([params.seed, 0xACCE97]),  # distinct salt from the flow/price RNG streams
        markout_tracker=MarkoutTracker(),
        response_tracker=ResponseRatioTracker(),
        compliance_log=ComplianceLog.empty(),
        book=InventoryBook(limits=params.inventory_limits),
        quotes=[],
        fills=[],
        filled_quote_ids=set(),
        hedges=[],
        outcomes=[],
        inventory_snapshots={isin: [] for isin in params.isins},
        killed_isins=set(),
        active_halts={},
        final_mark={},
    )

    day_start = venue_session.session_open(params.sim_date)
    for isin in params.isins:
        state.inventory_snapshots[isin].append((day_start, 0.0))

    lom_fire_seq = 0
    for ts, kind, payload in events:
        if kind == 0:
            assert isinstance(payload, RFQ)
            _process_rfq(
                payload,
                forced_accept_ts=None,
                venue_config=venue_config,
                venue_session=venue_session,
                reference_session=reference_session,
                feed=feed,
                instruments=instruments,
                flow_params=flow_params,
                sim_params=params,
                state=state,
            )
        elif kind == 1:
            assert isinstance(payload, LimitOrder)
            lom_book.add(payload)
        else:
            for isin in params.isins:
                if isin in state.killed_isins:
                    continue
                bbo = feed.bbo(ts, isin, 1_000.0)
                for order in lom_book.recheck(isin, bbo):
                    lom_fire_seq += 1
                    synthetic_rfq = RFQ(
                        rfq_id=f"lom-fire-{lom_fire_seq:06d}",
                        ts=ts,
                        isin=order.isin,
                        requester_id=order.requester_id,
                        side=order.side,
                        requested_size_eur=order.size_eur,
                        is_exempt=False,
                    )
                    _process_rfq(
                        synthetic_rfq,
                        forced_accept_ts=ts,
                        venue_config=venue_config,
                        venue_session=venue_session,
                        reference_session=reference_session,
                        feed=feed,
                        instruments=instruments,
                        flow_params=flow_params,
                        sim_params=params,
                        state=state,
                    )

    day_end = venue_session.session_close(params.sim_date) - timedelta(seconds=1)
    for isin in params.isins:
        closing_mark = state.final_mark.get(isin)
        if closing_mark is None:
            try:
                closing_mark = feed.bbo(day_end, isin, 1_000.0).mid
            except ValueError:
                closing_mark = 0.0
            state.final_mark[isin] = closing_mark
        closing_position = state.book.position_eur(isin, closing_mark)
        state.inventory_snapshots[isin].append((day_end, closing_position))

    return SimulationResult(
        sim_date=params.sim_date,
        rfqs=flow.rfqs,
        quotes=state.quotes,
        fills=state.fills,
        filled_quote_ids=state.filled_quote_ids,
        hedges=state.hedges,
        outcomes=state.outcomes,
        compliance_log=state.compliance_log,
        response_tracker=state.response_tracker,
        inventory_snapshots=state.inventory_snapshots,
        final_units={isin: state.book.units(isin) for isin in params.isins},
        final_mark=state.final_mark,
        killed_isins=state.killed_isins,
        lom_orders=lom_orders,
    )


def write_audit_trail(
    result: SimulationResult, instruments: dict[str, Instrument], audit_log: AuditLog
) -> None:
    """§15: one audit record per material event of the day — every fill
    (with FX rate/conversion formula for non-EUR instruments), withdrawal,
    firm-size breach, and regulatory-clamp event."""
    for fill in result.fills:
        instrument = instruments[fill.isin]
        fx_rate = instrument.fx_rate_to_eur
        fx_formula = "price_eur = price_local * fx_rate" if fx_rate is not None else None
        audit_log.record(
            fill.ts,
            "fill",
            fill.isin,
            {
                "fill_id": fill.fill_id,
                "quote_id": fill.quote_id,
                "side": fill.side.value,
                "price": fill.price,
                "size_eur": fill.size_eur,
                "requester_id": fill.requester_id,
                "is_informed_label": fill.is_informed_label,
            },
            fx_rate=fx_rate,
            fx_conversion_formula=fx_formula,
        )

    for withdrawal in result.compliance_log.withdrawal_events:
        audit_log.record(
            withdrawal.ts,
            "withdrawal",
            withdrawal.isin,
            {"quote_id": withdrawal.quote_id},
        )

    for breach in result.compliance_log.breach_events:
        audit_log.record(
            breach.ts,
            "firm_size_breach",
            breach.isin,
            {"quote_id": breach.quote_id, "halt_until": breach.halt_until.isoformat()},
        )

    for halted in result.compliance_log.halted_events:
        audit_log.record(
            halted.ts,
            "halt_blocked_rfq",
            halted.isin,
            {"rfq_id": halted.rfq_id, "halt_until": halted.halt_until.isoformat()},
        )

    for clamp in result.compliance_log.clamp_events:
        audit_log.record(
            clamp.ts,
            "clamp_bound",
            clamp.isin,
            {"risk_delta": clamp.risk_delta, "regulatory_cap": clamp.regulatory_cap},
        )
