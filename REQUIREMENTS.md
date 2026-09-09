# REQUIREMENTS — extracted from spec.md

One line per testable requirement. Tags: **[MUST]** hard requirement, **[SHOULD]** optional/best-effort per the spec's own wording, **[MUST NOT]** explicit exclusion. IDs are stable references for PLAN.md and commit messages.

## §0 — Domain rules (EIX Regelwerk)

- R1 [MUST] Do not implement a central limit order book / matching engine for RFQ execution.
- R2 [MUST] Model two execution paths: the market-maker (RFQ) system and a Limit Order Management (LOM) system (§3).
- R3 [MUST] MM must answer ≥95% of quote requests per calendar month (§5(1)).
- R4 [MUST] Response-ratio accounting processes quote requests in arrival order per instrument (FIFO per ISIN) (§5(1)).
- R5 [MUST] Implement the §5(2) exemption list from the quoting duty (types enumerated in R116a–i).
- R6 [MUST] Exemption windows are evaluated per request (§5(2)).
- R7 [MUST] Exempted requests are excluded from the response-ratio denominator (§5(2)).
- R8 [MUST] A Quote = ISIN + bid + ask + firm size (*Stillhaltevolumen*) (§6), implemented as a dataclass.
- R9 [MUST] Quoted spread must not lie outside the current reference-market spread for the requested size, after trading, settlement and FX costs (§7(1)).
- R10 [MUST] Guaranteed volumes are defined per segment, with distinct values for reference-market open vs. closed (§7(1)).
- R11 [MUST] Acceptance within 10 seconds of quote issuance forms the trade (§8(1)) — Quote TTL = 10s.
- R12 [MUST] MM may withdraw an accepted trade within 25 seconds if the market moved materially — defined as the accepted side now lying outside the current reference spread (§8(2)).
- R13 [MUST] Track a withdrawal-rate metric for §8(2) withdrawals.
- R14 [MUST] If aggregate fills on one quote exceed the firm size, MM may withdraw the breaching fill and all subsequent trades against that quote (§8(3)).
- R15 [MUST] A firm-size breach (§8(3)) triggers a halt of up to 1 minute.
- R16 [MUST] Each quote tracks a firm-size budget decremented as it is filled (§8(3)).
- R17 [MUST] LOM re-checks resting client limit orders against the MM price feed on every price update (§9).
- R18 [MUST] When a resting LOM order becomes marketable, it auto-fires an RFQ (§9).
- R19 [SHOULD] Inside-Match (§11): incoming orders check against resting LOM orders inside the MM spread, price-time priority, avoiding partial fills. Explicitly optional/cuttable per §7 build order.
- R20 [SHOULD] MM has the right but not the duty of self-entry in Inside-Match (§11).
- R21 [MUST] Detect abusive requester behaviour (§12): high-frequency quote requests.
- R22 [MUST] Detect abusive requester behaviour (§12): mass enter-then-cancel.
- R23 [SHOULD] Detect abusive requester behaviour (§12): manipulating the reference market to move the quote.
- R24 [SHOULD] §13 5-level message throttling is modelled in config only, not behaviourally enforced.
- R25 [MUST] Record all trading details in an immutable audit log, including FX rate and conversion formula for foreign reference markets (§15).
- R26 [MUST] Audit log is append-only / immutable (JSONL or Parquet) (§15).
- R27 [MUST] CLI command `mm audit export` lets surveillance export records on demand (§16).
- R28 [MUST] Trading session runs 08:00–22:00 CET, Monday–Friday.
- R29 [MUST] A 5-minute post-trading phase follows the 22:00 close.
- R30 [MUST] Guaranteed-volume table implemented exactly as specified for all 10 segment rows × 2 states (open/closed).
- R31 [MUST] Leveraged products' guaranteed volume scales down proportionally via a configurable factor (`leveraged_scaling`, default 0.5).
- R32 [MUST] Below €5/share, quoting is discretionary (§7(2)).
- R33 [MUST] Above the guaranteed volume, quoting is discretionary (§7(2)).
- R34 [MUST] README's first screen states plainly that every market-data path is a documented proxy.
- R194 [MUST] Venue-specific rules/parameters live exclusively in YAML config, never hard-coded in application code.

## §1 — Tech stack

- R35 [MUST] Language is Python 3.12.
- R36 [MUST] Package/env management via uv (`uv init`, `uv add`, `uv run`).
- R37 [SHOULD] A `requirements.txt`-based fallback path exists for environments without uv.
- R38 [MUST] pandas 2.x + NumPy used for data handling.
- R39 [MUST NOT] polars is not used unless a dataset exceeds memory (spec states this won't happen).
- R40 [MUST] Venue rulebook config modeled with Pydantic v2 + PyYAML, validated and typed.
- R41 [MUST] Config validation errors surface clearly/loudly.
- R42 [MUST] CLI built with Typer.
- R43 [MUST] CLI provides `mm run`.
- R44 [MUST] CLI provides `mm report`.
- R45 [MUST] CLI provides `mm sweep`.
- R46 [MUST] CLI provides `mm audit export`.
- R47 [MUST] CLI provides `mm validate` (implied by M1 done-when).
- R48 [MUST] Simulation core is a hand-rolled `heapq`-based discrete event queue.
- R49 [MUST NOT] SimPy is not used.
- R50 [MUST] Simulation is deterministic given a seed.
- R51 [MUST] Randomness generated via `numpy.random.default_rng(seed)`, threaded through every stochastic component.
- R52 [MUST] Every run is reproducible from a seed stored in config.
- R53 [MUST] Fills, quotes, and audit records persisted as Parquet via PyArrow.
- R54 [SHOULD] SQLite added only if a query layer is wanted.
- R55 [MUST] Plots built with Plotly.
- R56 [MUST] Static PNG export of plots via kaleido.
- R57 [MUST] Reports generated via Jinja2 templates rendered to HTML.
- R58 [MUST] HTML reports converted to PDF (weasyprint or print-to-pdf — mechanism ambiguous, see Q4).
- R59 [MUST] Tests use pytest.
- R60 [MUST] Property-based test (Hypothesis) for the tick-rounding invariant.
- R61 [MUST] Property-based test (Hypothesis) for the spread-clamp invariant.
- R62 [MUST] ruff used for linting and formatting.
- R63 [MUST] mypy strict mode applied to `core/` (scope of rest of tree ambiguous, see Q11).
- R64 [MUST] Logging via structlog, output as JSONL.
- R65 [MUST] Structured logs double as the §15 audit trail.
- R66 [MUST] CI (GitHub Actions) runs ruff.
- R67 [MUST] CI runs mypy.
- R68 [MUST] CI runs pytest.
- R69 [MUST] CI runs a 1-day simulation.
- R70 [MUST] CI uploads the report as a build artifact.
- R71 [MUST] Green CI badge shown on the README.
- R72 [SHOULD] Stretch: FastAPI `POST /rfq` returns a firm quote with 10s TTL — only if core is finished/tested.
- R73 [SHOULD] Stretch: FastAPI `POST /accept` fills or withdraws.
- R74 [SHOULD] Stretch: Streamlit dashboard.
- R75 [SHOULD] Stretch: Dockerfile + docker-compose.
- R76 [MUST NOT] No matching engine.
- R77 [MUST NOT] No ML models.
- R78 [MUST NOT] No C++/Rust components; README states this is a research simulator, not a latency-sensitive gateway.
- R79 [MUST NOT] No live broker connectivity.
- R80 [MUST NOT] No scraping of EIX, Börse Hannover, or Scalable pages.

## §2 — Market data

- R81 [MUST] `ReferenceMarketFeed` Protocol defines `bbo(ts, isin, size_eur) -> BBO`.
- R82 [MUST] `BBO` includes bid, ask, bid_size, ask_size, is_open, is_indicative.
- R83 [MUST] Three interchangeable feed adapters implement the protocol: `synthetic`, `bars`, `real_l1`.
- R84 [MUST] `synthetic` is the default feed, needs no network, always works.
- R85 [MUST] `synthetic` mid-price follows geometric Brownian motion.
- R86 [MUST] `synthetic` mid-price has U-shaped intraday volatility seasonality.
- R87 [MUST] `synthetic` spread widens with volatility.
- R88 [MUST] `synthetic` spread widens around open/close.
- R89 [MUST] `synthetic` output is deterministic given a seed.
- R90 [SHOULD] README frames `synthetic` mode as enabling exact adverse-selection measurement (ground-truth informed fraction).
- R91 [MUST] `bars` uses `yfinance` 1-minute bars for German tickers (e.g. SAP.DE, SIE.DE, EXS1.DE, IWDA.AS).
- R92 [MUST] `bars` caches fetched data to Parquet on first fetch for offline reproducibility.
- R93 [MUST NOT] `bars` does not invent a fixed spread from OHLC bars.
- R94 [MUST] `bars` estimates the effective bid-ask spread via the Corwin–Schultz high-low estimator.
- R95 [SHOULD] `bars` optionally cross-checks with Abdi–Ranaldo.
- R96 [SHOULD] README documents the Deutsche Börse Public Dataset as an alternative bar source (unmaintained, historical-only, non-commercial license).
- R97 [MUST] `real_l1` provides real quotes/spreads for headline markout/spread-capture figures.
- R98 [MUST] `real_l1` supports LOBSTER sample data (NASDAQ reconstructed L1: AAPL/AMZN/GOOG/INTC/MSFT).
- R99 [SHOULD] `real_l1` supports Alpaca free-tier historical US quotes (IEX) as an alternative.
- R100 [SHOULD] `real_l1` supports Databento as an alternative (subject to checking EU venue coverage).
- R101 [MUST] Headline `real_l1` config is run on ≥1 real day; the chart is labeled honestly (NASDAQ/LOBSTER as stand-in for unavailable Xetra L1).
- R102 [MUST] README states EIX has no public market-data API and Xetra real-time L1 is a paid product; neither is used.
- R103 [MUST] RFQ arrivals generated as a non-homogeneous Poisson process.
- R104 [MUST] Arrival intensity λ(t) follows a U-shaped intraday profile.
- R105 [MUST] Arrival intensity λ(t) includes a bump at 15:30 CET (US open).
- R106 [MUST] RFQ size drawn from a log-normal distribution.
- R107 [MUST] RFQ size calibrated so the median retail ticket is in the low hundreds of euros.
- R108 [MUST] RFQ size distribution has a fat tail into the guaranteed-volume range.
- R109 [MUST] RFQ direction defaults to 50/50.
- R110 [MUST] RFQ direction imbalance is configurable to stress inventory.
- R111 [MUST] Configurable informed fraction φ marks requests whose fill is followed by adverse fair-value drift.
- R112 [MUST] φ is a sweepable simulation parameter.
- R113 [MUST] A small population of abusive requesters issues high-frequency requests, for exercising §12 surveillance.

## §3 — Repository layout

- R114a [MUST] `src/rfq_mm/core/types.py` — Quote, RFQ, Fill, BBO, Position as frozen dataclasses.
- R114b [MUST] `src/rfq_mm/core/clock.py` — session calendar (08:00–22:00 CET, post-trading phase).
- R114c [MUST] `src/rfq_mm/core/ticks.py` — RTS 11 tick-size table + rounding.
- R114d [MUST] `src/rfq_mm/venue/config.py` — Pydantic models for the rulebook.
- R114e [MUST] `src/rfq_mm/venue/obligations.py` — §5 exemption windows, response-ratio accounting.
- R114f [MUST] `src/rfq_mm/venue/guarantees.py` — §7 segment→volume lookup.
- R114g [MUST] `src/rfq_mm/feed/base.py` — ReferenceMarketFeed protocol.
- R114h [MUST] `src/rfq_mm/feed/synthetic.py`.
- R114i [MUST] `src/rfq_mm/feed/bars.py` — yfinance + Corwin–Schultz.
- R114j [MUST] `src/rfq_mm/feed/real_l1.py` — LOBSTER/Alpaca loader.
- R114k [MUST] `src/rfq_mm/flow/generator.py` — arrivals, sizes, informed fraction, abusers.
- R114l [MUST] `src/rfq_mm/engine/quoter.py` — fair value, inventory skew, spread, size.
- R114m [MUST] `src/rfq_mm/engine/rfq.py` — 10s TTL, 25s withdrawal, firm-size budget.
- R114n [MUST] `src/rfq_mm/engine/lom.py` — client limit order book, rechecked on price update.
- R114o [SHOULD] `src/rfq_mm/engine/inside_match.py` — §11 (optional).
- R114p [MUST] `src/rfq_mm/risk/inventory.py` — limits, kill switch.
- R114q [MUST] `src/rfq_mm/risk/hedger.py` — hedge to reference market with slippage.
- R114r [MUST] `src/rfq_mm/compliance/monitor.py` — response ratio, spread cap, volume, withdrawal-rate monitoring.
- R114s [MUST] `src/rfq_mm/compliance/surveillance.py` — §12 abusive-requester detection.
- R114t [MUST] `src/rfq_mm/compliance/audit.py` — §15 immutable JSONL/Parquet log.
- R114u [MUST] `src/rfq_mm/analytics/metrics.py` — markouts, spread capture, fill ratio, P&L attribution.
- R114v [MUST] `src/rfq_mm/analytics/sweep.py` — parameter grid.
- R114w [MUST] `src/rfq_mm/report/templates/desk_report.html.j2` + `report/build.py`.
- R114x [MUST] `src/rfq_mm/cli.py` — Typer CLI.
- R115 [MUST] `venues/README.md` documents how to add a venue in ~20 lines.

## §4 — Venue config schema

- R116a [MUST] Exemption `after_venue_open`, `minutes: 15`.
- R116b [MUST] Exemption `around_reference_open`, `before_minutes: 10, after_minutes: 15`.
- R116c [MUST] Exemption `reference_halt`, `grace_minutes: 3`.
- R116d [MUST] Exemption `reference_indicative_only`.
- R116e [MUST] Exemption `corporate_action_cum_day`, `after: "19:55"`.
- R116f [MUST] Exemption `mm_system_outage`.
- R116g [MUST] Exemption `suspected_manipulation`.
- R116h [MUST] Exemption `conformity_check`, `max_seconds: 30`.
- R116i [MUST] Exemption `extreme_move`, `threshold_bps: 300, window_seconds: 60`.
- R116j [MUST] `surveillance.max_rfq_per_participant_per_minute: 60` (configurable).
- R116k [MUST] `surveillance.max_cancel_ratio: 0.9` (configurable).
- R116l [MUST] `throttling.levels: [none, slow_low_priority, reject_low_priority, reject_all, manual_reconnect]`, config only.
- R116m [MUST] `venues/eix.yaml` reproduces every field of the schema in §4 exactly (venue, session, reference_market, quote, obligations, spread, guaranteed_volumes_eur, surveillance, throttling).
- R117 [MUST] Adding a new venue requires writing only one YAML file — no code changes. If a rule can't be expressed in config, fix the schema, not the venue file.
- R118 [MUST] `venues/gettex.yaml` exists as a second venue config, present from day one.

## §5 — Quote engine algorithm

- R119 [MUST] Per-RFQ processing order: exemption check → fair value → reservation price → risk spread → regulatory clamp → tick rounding → size → issue.
- R120 [MUST] Exemption check (§5(2)): if exempt, the desk may still quote voluntarily.
- R121 [MUST] Voluntary quotes on exempt requests are excluded from the response-ratio denominator.
- R122 [MUST] Once given, a voluntary quote is binding.
- R123 [MUST] Voluntary quotes on exempt requests are not bound by the §7 quality/guaranteed-volume requirements.
- R124 [MUST] Exempt/voluntary quotes and obligated quotes are tracked as two separately identifiable paths.
- R125 [MUST] Fair value defaults to the reference mid price.
- R126 [MUST] Fair value may optionally use depth-weighted micro-price: `(bid·ask_size + ask·bid_size) / (bid_size + ask_size)`.
- R127 [MUST] Reservation price uses Avellaneda–Stoikov closed form: `r = fv − q·γ·σ²·(T−t)`.
- R128 [MUST NOT] The HJB equation is not solved numerically; only the closed form is used.
- R129 [MUST] Risk spread: `δ = γ·σ²·(T−t) + (2/γ)·ln(1 + γ/κ)`.
- R130 [MUST] Risk spread includes an adverse-selection premium scaled by recent realised markout.
- R131 [MUST] Regulatory clamp (§7(1)): quoted spread must not exceed the reference spread for the requested size, after costs.
- R132 [MUST] When risk spread exceeds the cap: quote at the cap (accept negative expected edge) or invoke a §5(2) exemption.
- R133 [MUST] Every clamp-binding event is logged.
- R134 [MUST] Clamp-binding events are charted.
- R135 [MUST] Tick rounding uses the RTS 11 table.
- R136 [MUST] Bid rounds down to the nearest valid tick.
- R137 [MUST] Ask rounds up to the nearest valid tick.
- R138 [MUST] Rounded bid/ask never cross.
- R139 [MUST] Quoted size = min(guaranteed volume for segment/state, remaining inventory headroom, per-quote cap).
- R140 [MUST] Each issued quote carries a 10s TTL.
- R141 [MUST] On acceptance, re-evaluate the §8(2) withdrawal condition.
- R142 [MUST] After withdrawal evaluation, re-evaluate the §8(3) firm-size budget.

## §6 — Metrics

- R143 [MUST] Markouts computed at 1s, 10s, 60s, 5min after each fill.
- R144 [MUST] Markout formula: `mo(τ) = side · (mid(t+τ) − fill_price) / mid(t) · 10⁴`, bps, signed by direction.
- R145 [MUST] Markout report includes mean, median, distribution.
- R146 [MUST] Markouts split by ticket-size bucket.
- R147 [MUST] Markouts split by requester.
- R148 [MUST] Markout chart is a headline chart in the report.
- R149 [MUST] Realised spread = quoted spread capture − 60s markout.
- R150 [MUST] Effective spread capture computed in bps, per fill.
- R151 [MUST] Effective spread capture computed volume-weighted.
- R152 [MUST] Quoted spread vs. reference spread tracked in bps over the day.
- R153 [MUST] §7 cap drawn as a line on that chart.
- R154 [MUST] Fill ratio = accepted / quoted, by size bucket.
- R155 [MUST] Response ratio (§5) computed excluding exempt requests, tracked against the 95% floor.
- R156 [MUST] Withdrawal rate (§8(2)) tracked.
- R157 [MUST] Inventory path tracked.
- R158 [MUST] Max absolute inventory tracked.
- R159 [MUST] Time-weighted inventory tracked.
- R160 [MUST] Hedge cost tracked.
- R161 [MUST] P&L attribution splits into spread capture / inventory MTM / hedge slippage / adverse selection.
- R162 [MUST] P&L attribution components reconcile to total P&L within one cent.
- R163 [MUST] A test asserts the reconciliation in R162.
- R164 [MUST] `mm sweep` produces a grid over γ × φ × inventory limit.
- R165 [MUST] Sweep reports P&L, Sharpe, max inventory, compliance breaches per grid point.
- R166 [MUST] Sweep grid rendered as a heatmap.

## §7 — Build order / acceptance criteria

- R167 [MUST] M1: `mm validate venues/eix.yaml` prints the parsed rulebook; a bad config fails loudly.
- R168 [MUST] M2: Hypothesis test confirms rounded bid ≤ raw bid, rounded ask ≥ raw ask, never crossed.
- R169 [MUST] M3: a full day of synthetic ticks streams deterministically from a seed.
- R170 [MUST] M4: arrival intensity matches the configured profile within a stated tolerance (test).
- R171 [MUST] M5: an end-to-end simulation produces fills and a P&L series.
- R172 [MUST] M6: compliance report lists zero breaches on a benign config, non-zero on a stressed config.
- R173 [MUST] M7: P&L attribution reconciles to total; the markout curve renders.
- R174 [MUST] M8: the same command runs on a real SAP.DE day (`bars`) and on the second venue config (`gettex`).
- R175 [MUST] M9: `mm report` emits a self-contained HTML/PDF desk report.
- R176 [SHOULD] Cut order if scope must shrink: Streamlit → FastAPI → inside_match.py (§11) → real_l1.py → throttling model.
- R177 [MUST NOT] Never cut: markouts, the compliance monitor, the README limitations section.

## §8 — Deliverables

- R178 [MUST] Public GitHub repo with green CI.
- R179 [MUST] README opens with a one-paragraph real-vs-simulated statement.
- R180 [MUST] `reports/desk_report_YYYY-MM-DD.pdf` produced for one day of simulated trading.
- R181 [MUST] Desk report PDF includes: P&L attribution, markout curve, quoted-vs-reference spread with §7 cap, inventory path, compliance-exceptions table.
- R182 [MUST] Desk report PDF is committed to the repo (viewable without running anything).
- R183 [MUST] `notebooks/01_results.ipynb` narrates: venue model, quoting policy, effect of rising informed fraction, where the spread cap forces a loss-making quote.
- R184 [MUST] A 2-page PDF summary is produced: EIX-vs-CLOB paragraph, one markout chart, one limitations paragraph.

## §9 — README requirements

- R185 [MUST] "What this is" statement present.
- R186 [MUST] "What is real" statement present (rulebook citation w/ date+sections, RTS 11, bars/real_l1 underlying price series).
- R187 [MUST] "What is simulated" statement present (RFQ flow, bars-mode spread, all fills, synthetic order book).
- R188 [MUST] "What this is not" statement present (not connected to EIX, not latency-sensitive, not affiliated with Scalable/BÖAG, not a reproduction, public documents only).
- R189 [MUST] "What I would do differently with real data" statement present.

## §10 — Portability

- R190 [MUST] Two venue configs ship from day one (duplicate cross-ref of R118).
- R191 [SHOULD] Docs note portability to gettex/Tradegate/L&S/mwb fairtrade.
- R192 [SHOULD] Docs note portability to crypto brokers.
- R193 [SHOULD] Docs note portability to broker-side/best-execution use case.

---

**Totals:** 193 individually numbered requirements (R1–R194, R114 expanded into sub-items) extracted from spec.md. See chat for the ambiguity/question list gating Phase 2.
