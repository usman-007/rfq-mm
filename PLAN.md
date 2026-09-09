# PLAN — rfq-market-maker

Scope decisions were made to keep this a working research simulator, not a production system. Where the spec itself lists items as optional/cuttable (§7 build-order table), they are deferred rather than stub-built — no placeholder functions, no fake data, per project rules. Every deviation from a literal spec reading is logged in **§4 Decisions** below with a requirement-ID cross-reference.

## 1. Repository layout

Matches `spec.md` §3 with the deferrals noted:

```
rfq-market-maker/
├── README.md
├── REQUIREMENTS.md
├── PLAN.md
├── pyproject.toml
├── uv.lock
├── requirements.txt            # generated via `uv export`, R37
├── venues/
│   ├── eix.yaml
│   ├── gettex.yaml
│   └── README.md
├── src/rfq_mm/
│   ├── core/
│   │   ├── types.py             # Quote, RFQ, Fill, BBO, Position, Instrument
│   │   ├── clock.py
│   │   └── ticks.py
│   ├── venue/
│   │   ├── config.py
│   │   ├── obligations.py
│   │   └── guarantees.py
│   ├── feed/
│   │   ├── base.py
│   │   ├── synthetic.py
│   │   └── bars.py
│   ├── flow/generator.py
│   ├── instruments/
│   │   └── universe.py          # curated fixture, see Decision D1
│   ├── engine/
│   │   ├── quoter.py
│   │   └── rfq.py
│   ├── risk/
│   │   ├── inventory.py
│   │   └── hedger.py
│   ├── compliance/
│   │   ├── monitor.py
│   │   ├── surveillance.py
│   │   └── audit.py
│   ├── analytics/
│   │   ├── metrics.py
│   │   └── sweep.py
│   ├── report/
│   │   ├── templates/desk_report.html.j2
│   │   ├── templates/summary_2page.html.j2
│   │   └── build.py
│   └── cli.py
├── tests/
│   ├── unit/…
│   └── property/…
├── notebooks/01_results.ipynb
├── reports/desk_report_YYYY-MM-DD.pdf     # committed output
├── docs/summary_2page.pdf                  # committed output
└── .github/workflows/ci.yml
```

**Not created in this pass** (see Decision D3): `feed/real_l1.py`, `engine/inside_match.py`, any FastAPI app, any Streamlit app, `Dockerfile`/`compose.yaml`. Nothing stubbed — these files simply don't exist until built for real.

## 2. Data model (`core/types.py`, frozen dataclasses unless noted)

- `Instrument`: `isin, name, segment: Segment, currency, is_leveraged: bool, fx_rate: float | None` (D2)
- `BBO`: `ts, isin, bid, ask, bid_size, ask_size, is_open, is_indicative`
- `RFQ`: `rfq_id, ts, isin, requester_id, side, requested_size_eur, is_exempt, exemption_type: str | None`
- `Quote`: `quote_id, rfq_id, ts_issued, isin, bid, ask, firm_size_eur, remaining_firm_size_eur (mutable ledger, tracked outside the frozen dataclass via engine state), ttl_expiry_ts, is_voluntary, status`
- `Fill`: `fill_id, quote_id, ts, isin, side, price, size_eur, requester_id, is_informed_label, withdrawn: bool`
- `Position`: `isin, quantity, avg_price, ts`
- `ComplianceEvent`: `ts, category, severity, detail` (response ratio, spread-cap clamp, withdrawal, surveillance, firm-size breach)
- `AuditRecord`: full JSONL row per material event — quote issued, fill, withdrawal, exemption invoked, clamp bound, breach — including `fx_rate`/`conversion_formula` for non-EUR instruments (§15).

`Quote`/`Fill` are 1:many (Decision D8): a `Quote` carries a mutable `remaining_firm_size_eur` counter in engine state; each accepted `Fill` decrements it; a `Fill` that would take cumulative size past the original `firm_size_eur` is the breaching fill (§8(3)).

## 3. Ordered implementation steps

Each step: implement → run `ruff check`, `mypy`, `pytest` → report pass/fail → commit with requirement IDs → only then proceed.

| Step | Scope | Satisfies |
|---|---|---|
| **S1** | `uv init`, `pyproject.toml`, ruff/mypy config, pytest scaffold, CI skeleton (lint+type+test job only), `venue/config.py` Pydantic models, `venues/eix.yaml`, `venues/gettex.yaml` (minimal valid config for now, filled out at S8), `venues/README.md`, `mm validate` | R35,R36,R38,R40-R47,R59,R62-R65,R114d,R116a-m,R117,R118,R194,R167 |
| **S2** | `core/types.py`, `core/clock.py` (session calendar), `core/ticks.py` (RTS 11 — **verify real Annex Table 3 values via web search before hardcoding, cite source**; if unverifiable, use a clearly-labeled illustrative table and say so), Hypothesis property tests | R114a,b,c,R8,R28,R29,R135-138,R60,R168 |
| **S3** | `feed/base.py` protocol, `feed/synthetic.py` (GBM mid + U-shaped vol seasonality + spread process), hand-rolled `heapq` event loop, seeded RNG threading | R81-R90,R48-R52,R114g,h,R169 |
| **S4** | `flow/generator.py`: NHPP arrivals (U-shape + 15:30 bump), log-normal sizes, direction imbalance, informed-fraction labeling, abusive-requester population; `instruments/universe.py` fixture (Decision D1) | R103-R113,R114k,R170 |
| **S5** | `venue/guarantees.py` (§7 lookup, all 15 segments, leveraged scaling, discretionary bands), `venue/obligations.py` (§5 exemptions + response-ratio FIFO/ISIN accounting), `engine/quoter.py` (fair value → reservation price → risk spread → clamp → tick rounding → size), `engine/rfq.py` (issue, TTL, §8(2) withdrawal, §8(3) firm-size budget) | R3-R18,R30-R33,R114e,f,l,m,R119-R142,R171 |
| **S6** | `risk/inventory.py` (limits, kill switch), `risk/hedger.py` (hedge + slippage), `compliance/monitor.py`, `compliance/surveillance.py` (§12, simplified per Decision D5), `compliance/audit.py` (JSONL/Parquet, immutable) | R21-R27,R114p,q,r,s,t,R172 |
| **S7** | `analytics/metrics.py` (markouts, realised spread, effective spread capture, fill ratio, response ratio, withdrawal rate, inventory stats, P&L attribution + reconciliation test), `analytics/sweep.py` (γ×φ×inventory-limit grid) | R143-R166,R114u,v,R173,R161-R163 |
| **S8** | `feed/bars.py` (yfinance 1m bars, Parquet cache, Corwin–Schultz spread estimator), finalize `venues/gettex.yaml` with real values, prove same CLI runs on both venues | R91-R96,R114i,R174,R190 |
| **S9** | `report/build.py` + `desk_report.html.j2` (Jinja2→HTML→weasyprint PDF), `docs/summary_2page.pdf` template, full README (all 5 required sections), `notebooks/01_results.ipynb` (authored, then executed once via `nbclient` so outputs are committed) | R55-R58,R114w,R175,R178-R189,R34 |
| **S10** | Finish `.github/workflows/ci.yml`: ruff → mypy → pytest → 1-day sim → upload report artifact; README badge | R66-R71 |
| **S11** | Re-read `spec.md` line by line against `REQUIREMENTS.md`; report anything skipped, changed, or deferred | DONE MEANS clause |

After S11, core build is "done." `real_l1.py` (LOBSTER) is then attempted as a best-effort follow-up (network/data permitting) and reported on separately — not blocking.

## 4. Decisions (resolving the 13 open questions, biased toward less complexity)

- **D1 (instrument universe):** curated fixture, ~15 real ISINs across DAX/MDAX/ETF/FUND + 2 synthetic foreign-segment instruments for unit-testing the guaranteed-volume table only (not live-simulated).
- **D2 (foreign FX):** static `fx_rate` per instrument in the fixture, no live FX feed. Foreign-segment instruments are excluded from the live day-sim; FX conversion + audit recording (§15) is proven by unit test with a synthetic foreign instrument.
- **D3 (scope cut):** FastAPI, Streamlit, Docker, `inside_match.py` (§11), and `real_l1.py` are **not built** in this pass — matches the spec's own cut-list and keeps complexity down. Nothing is stubbed; these simply don't exist yet.
- **D4 (PDF):** weasyprint. System packages documented in README; added to CI via apt.
- **D5 (§12 manipulation detector):** implemented as a simplified heuristic (correlate a requester's RFQ timing with abnormal reference-market moves in a short window), clearly labeled in README as a simplified proxy — full detection isn't meaningful against a simulated feed.
- **D6 (notebook):** authored then executed once with `nbclient`/`ipykernel` before commit, so GitHub renders outputs without the reader running anything.
- **D7 (2-page PDF):** committed at `docs/summary_2page.pdf`.
- **D8 (Quote:Fill cardinality):** 1:many, see §2 data model.
- **D9 (response ratio horizon):** monitor is horizon-agnostic; headline `mm run` defaults to one day; report labels the figure "day-to-date," not monthly.
- **D10 (uv/pip fallback):** `requirements.txt` generated via `uv export`, regenerated on dependency changes.
- **D11 (mypy scope):** `--strict` on `core/` only; normal mode on the rest of `src/rfq_mm/`.
- **D12 (φ outside synthetic):** not applicable in this pass — only `synthetic` and `bars` tiers are built, and `bars` uses a fixed historical path, so φ there only labels requests for post-hoc markout-split reporting; no synthetic drift is injected onto real prices.
- **D13 (§11/M7 cross-reference):** noted as a stale reference in the spec; treated as governed by the cut-list, not M7.
- **D14 (§9 LOM, found missing at the S11 audit):** `engine/lom.py` (R2/R17/R18/R114n) fell out of PLAN.md's file tree during Phase 2 without ever being flagged as a deviation — unlike D3's cuts, it isn't on spec.md's own cut-list. Confirmed with the user at S11 and built as S12: a resting client-limit-order book per ISIN, rechecked against the feed on every price tick, auto-firing an RFQ (through the existing `process_rfq`/`accept_quote` pipeline) when a resting order becomes marketable.
- **D15 (§8(3) halt enforcement, found incomplete at the S11 audit):** `halt_until` was computed and audited but never behaviourally enforced — nothing stopped new quotes on a halted ISIN during the window. Confirmed with the user at S11: enforce it (skip quoting on the halted ISIN until `halt_until`), and exclude halt-blocked RFQs from the §5(1) response-ratio denominator — the same treatment as a §5(2) exemption, since a regulatory-triggered halt isn't a discretionary miss. Honesty note: a real §8(3) breach requires multiple acceptances against one quote, which `run_day_simulation`'s one-fill-per-quote flow structurally cannot produce (see `engine/simulation.py::_active_halt`'s docstring) — the enforcement path is real and unit-tested but not exercised end-to-end by `mm run` today.

## 5. Exact stack & dependencies

Only what `spec.md` names, plus two small, justified additions for a spec-mandated deliverable it doesn't otherwise cover:

**Runtime:** `pandas>=2`, `numpy`, `pydantic>=2`, `pyyaml`, `typer`, `pyarrow`, `plotly`, `kaleido`, `jinja2`, `weasyprint`, `structlog`, `yfinance`
**Dev/test:** `pytest`, `hypothesis`, `ruff`, `mypy`
**Added, not named in spec — needed to actually produce `notebooks/01_results.ipynb` with committed output, which the spec requires but names no tool for:** `nbformat`, `nbclient`, `ipykernel`. No alternative in the spec's own list covers this, and it's standard, low-risk tooling — flagging per the "ask before adding a library" rule, then proceeding as directed.

No polars, no SimPy, no FastAPI/Streamlit/docker SDKs (deferred per D3), no `requests`/`httpx` (LOBSTER fetch deferred; stdlib `urllib` would be used if/when attempted).

Package manager: `uv`. `python-version`: 3.12.

---

Proceeding to Step 1.
