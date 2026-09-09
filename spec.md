# rfq-market-maker — Project Spec

**A quote-driven (RFQ) market-making engine with a venue-rulebook compliance monitor, modelled on the European Investor Exchange (EIX) market-maker model.**

This spec is written to be handed directly to Claude Code. Build in the order given. Everything venue-specific lives in YAML config, never in code.

---

## 0. Context for the implementer

EIX (European Investor Exchange, operated by BÖAG Börsen AG at Börse Hannover, technical operation outsourced to Scalable AG, market makers: Scalable Capital Bank GmbH and mwb fairtrade) is **not a central limit order book**. Do not build a matching engine. It is a **quote-driven RFQ venue anchored to a reference market** (Xetra for German names).

The mechanism, from the EIX Regelwerk (version of 9 December 2024):

| § | Rule | What it means in code |
|---|---|---|
| §3 | Prices form via market-maker system plus a Limit Order Management system | Two execution paths, not one book |
| §5(1) | MM must answer ≥95% of quote requests per calendar month, in arrival order per instrument | Response-ratio counter with a FIFO queue per ISIN |
| §5(2) | Long exemption list from the quoting duty | Exemption windows evaluated per request; exempted requests excluded from the ratio denominator |
| §6 | Quote = ISIN + bid + ask + firm size (*Stillhaltevolumen*) | Quote dataclass |
| §7(1) | Spread must not lie outside the current reference-market spread for the requested size, after trading, settlement and FX costs | Hard clamp on quoted spread |
| §7(1) | Guaranteed volumes per segment, different when reference market is open vs closed | Segment → (open, closed) €-volume table |
| §8(1) | Acceptance within **10 s** of quote issuance forms the trade | Quote TTL |
| §8(2) | MM may withdraw within **25 s** if the market moved materially — defined as the accepted side now lying outside the current reference spread | Withdrawal logic + withdrawal-rate metric |
| §8(3) | If aggregate fills on one quote exceed the firm size, MM may withdraw the breaching and all subsequent trades; triggers a halt of up to 1 minute | Firm-size budget per quote |
| §9 | LOM re-checks resting limit orders against the MM price feed on **every** price update; when marketable it auto-fires an RFQ | Event loop tick |
| §11 | Inside-Match: incoming orders check against resting LOM orders inside the MM spread, price-time priority, avoiding partial fills; MM has the *right but not the duty* of self-entry | Optional module, see M7 |
| §12 | Abuse: high-frequency quote requests, mass enter-then-cancel, manipulating the reference market to move the quote | Requester-behaviour detector |
| §13 | 5-level message throttling (0 none → 4 manual reconnect) | Optional, config-only |
| §15 | MM must record all trading details, including FX rate and conversion formula for foreign reference markets | Immutable audit log |
| §16 | Surveillance may demand those records | `audit export` CLI command |
| — | Trading 08:00–22:00 CET Mon–Fri, plus a 5-minute post-trading phase | Session calendar |

Guaranteed volumes (€, reference market open / closed):

| Segment | Open | Closed |
|---|---|---|
| DAX | 100,000 | 50,000 |
| MDAX / SDAX / TecDAX | 50,000 | 25,000 |
| Other domestic equities | 25,000 | 10,000 |
| Dow Jones 30 | 50,000 | 25,000 |
| Other US equities | 50,000 | 25,000 |
| EuroStoxx 50 | 100,000 | 50,000 |
| Other foreign equities | 25,000 | 10,000 |
| DAX ETFs | 500,000 | 100,000 |
| Other ETFs / ETCs / ETPs | 100,000 | 20,000 |
| Investment funds | 50,000 | 25,000 |

Leveraged products scale down proportionally. Below €5/share and above the guaranteed volume, quoting is discretionary (§7(2)).

**Honesty rule for this project:** we have no EIX market data and no EIX connectivity. Every data path is a documented proxy. The README must say so plainly in the first screen. Overclaiming is the fastest way to fail a quant screen.

---

## 1. Tech stack

Chosen for: fast to build in a day, boring and defensible, matches what a fintech desk actually runs.

### Core
| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Named in the JD; the whole ecosystem is here |
| Package/env | **uv** (`uv init`, `uv add`, `uv run`) | One tool, lockfile, ~10× faster than poetry; falls back to plain `pip -r requirements.txt` if unavailable |
| Data | **pandas 2.x + NumPy** | Named in the JD. Use `polars` only if a dataset exceeds memory — it won't |
| Config | **Pydantic v2 + PyYAML** | Venue rulebook as validated, typed config; validation errors are part of the demo |
| CLI | **Typer** | `mm run`, `mm report`, `mm sweep`, `mm audit export` |
| Simulation core | **`heapq` event queue, hand-rolled** | Deterministic, zero dependencies, easy to unit-test. Do **not** pull in SimPy |
| Randomness | **`numpy.random.default_rng(seed)`** threaded through every component | Every run must be reproducible from a seed in the config |
| Storage | **Parquet via PyArrow** for fills/quotes/audit; SQLite only if a query layer is wanted | Columnar, fast, portable |
| Plots | **Plotly** (static PNG export via kaleido) | Interactive HTML report + PNGs for the PDF |
| Report | **Jinja2 → HTML → PDF** (weasyprint or `--print-to-pdf`) | The JD says "you create reports and firmwide market updates" — deliver exactly that |
| Tests | **pytest**, plus **Hypothesis** for the tick-rounding and spread-clamp invariants | Property tests on rulebook invariants read very well to a quant |
| Quality | **ruff** (lint+format), **mypy** strict on `core/` | Signals engineering discipline |
| Logging | **structlog** → JSONL | Doubles as the §15 audit trail |
| CI | **GitHub Actions**: ruff → mypy → pytest → run a 1-day sim → upload report artifact | Green badge on the README |

### Optional stretch (only if the core is finished and tested)
| Layer | Choice | Why |
|---|---|---|
| Quote service | **FastAPI** — `POST /rfq` returns a firm quote with a 10 s TTL, `POST /accept` fills or withdraws | Turns the simulator into a service; directly showcases backend experience |
| Dashboard | **Streamlit** | Nice, but a clean static HTML report beats a half-finished dashboard |
| Container | **Dockerfile + compose** | Reproducibility; cheap to add last |

### Explicitly out of scope
No matching engine. No ML models. No C++/Rust "for speed" — this is a research simulator, not a latency-sensitive gateway; say so in the README rather than pretending otherwise. No live broker connectivity. No scraping of EIX, Börse Hannover or Scalable pages (terms of use, and it adds nothing).

---

## 2. Where the market data comes from

Three interchangeable adapters behind one interface. This is the single most important design decision in the project: it makes the project runnable by anyone, honest about its limits, and portable to other venues.

```python
class ReferenceMarketFeed(Protocol):
    def bbo(self, ts: datetime, isin: str, size_eur: float) -> BBO: ...
    # BBO = bid, ask, bid_size, ask_size, is_open, is_indicative
```

### Tier 1 — `synthetic` (default, no network, always works)
Geometric Brownian motion mid-price with intraday volatility seasonality (U-shaped), plus a spread process that widens with volatility and around open/close. Deterministic from a seed.

- **Use for:** unit tests, CI, parameter sweeps, anything needing thousands of runs.
- **Honest framing:** "a controlled environment where the true fair value is known, which lets me measure adverse selection exactly."
- This is a feature, not a fallback: with synthetic data you *know* the informed-flow fraction, so you can validate that your markout estimator recovers it.

### Tier 2 — `bars` (real prices, synthesised spread)
`yfinance` 1-minute bars for German tickers (`SAP.DE`, `SIE.DE`, `EXS1.DE`, `IWDA.AS`, …).

- **Constraint:** Yahoo serves 1-minute data for roughly the last 7 days, 2m/5m for ~60 days. Cache to Parquet on first fetch so runs are reproducible offline.
- **Bars have no bid/ask.** Do not invent a fixed spread. Estimate it from the data with the **Corwin–Schultz high–low spread estimator** (and optionally cross-check with Abdi–Ranaldo). Both are standard published estimators of the effective bid-ask spread from OHLC data. Citing them in the README is a strong signal — it shows you know the spread is estimated and you chose a defensible estimator.
- Alternative bar source: the **Deutsche Börse Public Dataset** (Xetra/Eurex 1-minute bars, public S3 bucket, EU-Central). Note in the README that the provider no longer maintains it, so it is historical-only and licensed non-commercial.

### Tier 3 — `real_l1` (real quotes, real spreads — for the headline numbers)
Where real bid/ask is needed — markouts and spread-capture figures are only fully credible against a real quoted spread — use a free real-L1 source, accepting that it will not be a German venue:

- **LOBSTER sample data** — free sample days of NASDAQ level-10 reconstructed order books (AAPL, AMZN, GOOG, INTC, MSFT). Real BBO, real depth, message-by-message. Best available free option.
- **Alpaca free tier** — historical US quotes (IEX), API key, no cost.
- **Databento** — free starting credits and clean L1/MBP schemas; check current European venue coverage before depending on it.

Run the headline configuration on one real-L1 day and label the chart honestly: *"Reference market: NASDAQ (LOBSTER sample), used as a stand-in for Xetra L1, which is not publicly available."*

### What is deliberately not used
EIX itself publishes no public market-data API, and Xetra real-time L1 is a paid licensed product. State this in the README rather than leaving the reader to wonder whether you tried.

### RFQ flow data
No public dataset of retail RFQ arrivals exists. It is simulated, with the generator parameterised and documented:

- Arrivals: non-homogeneous Poisson, intensity λ(t) with a U-shaped intraday profile plus a bump at 15:30 CET (US open).
- Size: log-normal, calibrated so the median retail ticket is in the low hundreds of euros with a fat tail into the guaranteed-volume range.
- Direction: 50/50 baseline, with a configurable buy/sell imbalance to stress inventory.
- **Informed fraction φ**: a share of requests whose fill is followed by a drift in the true fair value against the market maker. This is the key parameter of the whole model. Sweep it.
- Abusive requesters: a small population issuing high-frequency requests (§12), used to test the surveillance module.

---

## 3. Repository layout

```
rfq-market-maker/
├── README.md                  # what it is, what's real, what's simulated — first screen
├── pyproject.toml
├── venues/
│   ├── eix.yaml               # EIX rulebook as config
│   ├── gettex.yaml            # second venue — proves generality
│   └── README.md              # how to add a venue in ~20 lines
├── src/rfq_mm/
│   ├── core/
│   │   ├── types.py           # Quote, RFQ, Fill, BBO, Position (frozen dataclasses)
│   │   ├── clock.py           # session calendar, 08:00–22:00 CET, post-trading phase
│   │   └── ticks.py           # MiFID II RTS 11 tick-size table + rounding
│   ├── venue/
│   │   ├── config.py          # Pydantic models for the rulebook
│   │   ├── obligations.py     # §5 exemption windows, response-ratio accounting
│   │   └── guarantees.py      # §7 segment → volume lookup
│   ├── feed/
│   │   ├── base.py            # ReferenceMarketFeed protocol
│   │   ├── synthetic.py
│   │   ├── bars.py            # yfinance + Corwin–Schultz
│   │   └── real_l1.py         # LOBSTER / Alpaca loader
│   ├── flow/generator.py      # RFQ arrivals, sizes, informed fraction, abusers
│   ├── engine/
│   │   ├── quoter.py          # fair value, inventory skew, spread, size
│   │   ├── rfq.py             # 10 s TTL, 25 s withdrawal, firm-size budget
│   │   ├── lom.py             # limit order book of client orders, re-check on price update
│   │   └── inside_match.py    # §11 — optional
│   ├── risk/
│   │   ├── inventory.py       # limits, kill switch
│   │   └── hedger.py          # hedge to reference market with slippage
│   ├── compliance/
│   │   ├── monitor.py         # response ratio, spread cap, volume, withdrawal rate
│   │   ├── surveillance.py    # §12 abusive-requester detection
│   │   └── audit.py           # §15 immutable JSONL/Parquet log
│   ├── analytics/
│   │   ├── metrics.py         # markouts, spread capture, fill ratio, P&L attribution
│   │   └── sweep.py           # parameter grid
│   ├── report/
│   │   ├── templates/desk_report.html.j2
│   │   └── build.py
│   └── cli.py
├── tests/
├── notebooks/01_results.ipynb  # the narrative walkthrough
└── .github/workflows/ci.yml
```

---

## 4. Venue config schema (`venues/eix.yaml`)

```yaml
venue:
  name: "EIX (European Investor Exchange)"
  model: rfq_with_reference_market
  operator: "BÖAG Börsen AG / Börse Hannover"
  source: "Regelwerk European Investor Exchange, 09.12.2024"

session:
  timezone: Europe/Berlin
  open: "08:00"
  close: "22:00"
  post_trading_minutes: 5
  days: [mon, tue, wed, thu, fri]

reference_market:
  default: XETR
  hours: { open: "09:00", close: "17:30" }

quote:
  acceptance_window_seconds: 10        # §8(1)
  withdrawal_window_seconds: 25        # §8(2)
  firm_size_breach_halt_seconds: 60    # §8(3)

obligations:
  min_response_ratio: 0.95             # §5(1), per calendar month
  exemptions:                          # §5(2)
    - { type: after_venue_open,        minutes: 15 }
    - { type: around_reference_open,   before_minutes: 10, after_minutes: 15 }
    - { type: reference_halt,          grace_minutes: 3 }
    - { type: reference_indicative_only }
    - { type: corporate_action_cum_day, after: "19:55" }
    - { type: mm_system_outage }
    - { type: suspected_manipulation }
    - { type: conformity_check,        max_seconds: 30 }
    - { type: extreme_move,            threshold_bps: 300, window_seconds: 60 }

spread:
  rule: not_outside_reference_spread   # §7(1)
  include_costs: [trading, settlement, fx]
  depth_aware: true                    # spread for the *requested size*

guaranteed_volumes_eur:                # §7(1)
  DAX:            { ref_open: 100000, ref_closed: 50000 }
  MDAX:           { ref_open:  50000, ref_closed: 25000 }
  SDAX:           { ref_open:  50000, ref_closed: 25000 }
  TecDAX:         { ref_open:  50000, ref_closed: 25000 }
  DOMESTIC_OTHER: { ref_open:  25000, ref_closed: 10000 }
  DJ30:           { ref_open:  50000, ref_closed: 25000 }
  US_OTHER:       { ref_open:  50000, ref_closed: 25000 }
  ESTX50:         { ref_open: 100000, ref_closed: 50000 }
  FOREIGN_OTHER:  { ref_open:  25000, ref_closed: 10000 }
  ETF_DAX:        { ref_open: 500000, ref_closed: 100000 }
  ETF_OTHER:      { ref_open: 100000, ref_closed: 20000 }
  ETC:            { ref_open: 100000, ref_closed: 20000 }
  ETP:            { ref_open: 100000, ref_closed: 20000 }
  FUND:           { ref_open:  50000, ref_closed: 25000 }
  leveraged_scaling: 0.5
  discretionary_below_price_eur: 5.0   # §7(2)

surveillance:                          # §12
  max_rfq_per_participant_per_minute: 60
  max_cancel_ratio: 0.9

throttling:                            # §13 — modelled, not enforced
  levels: [none, slow_low_priority, reject_low_priority, reject_all, manual_reconnect]
```

Adding a venue must mean writing one of these files, nothing else. If a rule cannot be expressed in config, the config schema is wrong — fix the schema, not the venue.

---

## 5. The quote engine

Per RFQ, in this order:

1. **Exemption check** (§5(2)) — if exempt, the desk *may* still quote voluntarily; voluntary quotes are excluded from the response-ratio denominator but, once given, are binding and (per §5) not bound by the §7 quality and guaranteed-volume requirements. Track both paths separately.
2. **Fair value** — reference mid, optionally micro-price weighted by reference-market depth: `(bid·ask_size + ask·bid_size) / (bid_size + ask_size)`.
3. **Reservation price** — Avellaneda–Stoikov style inventory skew: `r = fv − q·γ·σ²·(T−t)`, where `q` is inventory in units, `γ` risk aversion, `σ` reference volatility. Keep the closed form; do not solve the HJB numerically.
4. **Risk spread** — `δ = γ·σ²·(T−t) + (2/γ)·ln(1 + γ/κ)`, plus an explicit adverse-selection premium scaled by recent realised markout.
5. **Regulatory clamp** (§7(1)) — quoted spread must not exceed the reference spread for the requested size, after costs. If the risk spread is wider than the cap, the desk faces a real choice: quote at the cap and accept negative expected edge, or invoke a §5(2) exemption. **Log every such event.** This tension is the most interesting thing in the project — make it a chart.
6. **Tick rounding** — RTS 11 table, bid down, ask up, never crossing.
7. **Size** — `min(guaranteed volume for segment/state, remaining inventory headroom, per-quote cap)`.
8. **Issue** with a 10 s TTL; on acceptance, re-evaluate §8(2) withdrawal, then §8(3) firm-size budget.

---

## 6. Metrics (`analytics/metrics.py`)

These are what a trading desk will actually read.

- **Markouts** at 1 s, 10 s, 60 s, 5 min after each fill, in bps, signed by direction: `mo(τ) = side · (mid(t+τ) − fill_price) / mid(t) · 10⁴`. Report mean, median and distribution, split by ticket size bucket and by requester. **This is the headline chart.**
- **Realised spread** = quoted spread capture minus the 60 s markout. The number that says whether the desk actually makes money.
- **Effective spread capture**, bps, per fill and volume-weighted.
- **Quoted spread vs. reference spread**, bps, over the day — with the §7 cap drawn as a line.
- **Fill ratio** = accepted / quoted, by size bucket.
- **Response ratio** (§5) with exempt requests excluded, tracked toward the 95% floor.
- **Withdrawal rate** (§8(2)) — a rising rate means quotes are too slow or too tight.
- **Inventory path**, max absolute inventory, time-weighted inventory, hedge cost.
- **P&L attribution**: spread capture / inventory mark-to-market / hedge slippage / adverse selection. Must reconcile to total P&L within a cent — assert this in a test.
- **Sensitivity grid** (`mm sweep`): γ (risk aversion) × informed fraction φ × inventory limit → P&L, Sharpe, max inventory, compliance breaches. Heatmap.

---

## 7. Build order and time budget (~8 hours)

| # | Milestone | Time | Done when |
|---|---|---|---|
| M1 | Repo, uv, ruff/mypy/pytest, CI skeleton, `venues/eix.yaml` + Pydantic loader | 45 min | `mm validate venues/eix.yaml` prints the parsed rulebook; bad config fails loudly |
| M2 | `core/`: types, session clock, RTS 11 ticks. Hypothesis tests on tick rounding | 45 min | Property test: rounded bid ≤ raw bid, rounded ask ≥ raw ask, never crossed |
| M3 | `feed/synthetic.py` + event loop | 60 min | A day of ticks streams deterministically from a seed |
| M4 | `flow/generator.py`: arrivals, sizes, informed fraction, abusers | 45 min | Arrival intensity matches the configured profile within tolerance |
| M5 | `engine/quoter.py` + `engine/rfq.py`: skew, spread, clamp, TTL, withdrawal, firm-size budget | 120 min | End-to-end sim produces fills and a P&L series |
| M6 | `risk/` + `compliance/`: inventory limits, hedger, response ratio, breach detection, audit log | 60 min | Compliance report lists zero breaches on a benign config and non-zero on a stressed one |
| M7 | `analytics/`: markouts, attribution, sweep | 90 min | P&L attribution reconciles to total; markout curve renders |
| M8 | `feed/bars.py` (yfinance + Corwin–Schultz), `venues/gettex.yaml` | 45 min | Same command runs on a real SAP.DE day and on a second venue config |
| M9 | Report + README | 60 min | `mm report` emits a self-contained HTML/PDF desk report |

**If time runs short, cut in this order:** Streamlit → FastAPI → `inside_match.py` (§11) → `real_l1.py` → throttling model. **Never cut:** markouts, the compliance monitor, or the README's limitations section.

---

## 8. Deliverables

1. **Public GitHub repo**, green CI, README that opens with a one-paragraph statement of what is real and what is simulated.
2. **`reports/desk_report_YYYY-MM-DD.pdf`** — one day of simulated trading: P&L attribution, markout curve, quoted-vs-reference spread with the §7 cap, inventory path, compliance exceptions table. Committed so a reader sees output without running anything.
3. **`notebooks/01_results.ipynb`** — the narrative: here is the venue model, here is the quoting policy, here is what happens as the informed fraction rises, here is where the regulatory spread cap forces a loss-making quote.
4. **2-page PDF summary** for the application: one paragraph on why EIX is RFQ-with-reference-market rather than a CLOB, one markout chart, one paragraph on limitations.

---

## 9. README requirements (non-negotiable)

The README must contain, near the top:

- **What this is:** a research simulator of a quote-driven market-making desk on a reference-market-anchored RFQ venue, with venue rules expressed as configuration.
- **What is real:** the venue rulebook (public EIX Regelwerk, cited with date and section numbers), the tick-size regime (RTS 11), and, in the `bars`/`real_l1` modes, the underlying price series.
- **What is simulated:** all RFQ flow; the bid/ask spread in `bars` mode (estimated via Corwin–Schultz); all fills; the entire order book in `synthetic` mode.
- **What this is not:** not connected to EIX, not a latency-sensitive production gateway, not affiliated with or endorsed by Scalable Capital or BÖAG, and not a reproduction of any proprietary system. Built from public documents only.
- **What I would do differently with real data:** calibrate the arrival intensity and informed fraction against actual RFQ logs, use real L1 depth for the size-aware spread cap, and measure withdrawal rates against real acceptance latencies.

That last section is what separates a student project from something a desk takes seriously.

---

## 10. Portability

Retargeting is a config change plus, at most, one feed adapter:

- **gettex** (Baader Bank as market maker), **Tradegate**, **Lang & Schwarz / LS Exchange**, **mwb fairtrade** — same quote-driven German venue model, different reference market and volume tables.
- **Crypto brokers** (Bitpanda, Bison) — drop the rulebook module, keep the inventory, adverse-selection and markout machinery unchanged; the spread policy becomes internal rather than regulatory.
- **Broker-side / best-execution roles** — the same fill and reference-market data, inverted: instead of "is my quote profitable?", ask "were our clients' fills competitive against the reference venue?" Same code, different report template.

Ship with **two venue configs from day one**. A generality claim with one config is an assertion; with two it is demonstrated.