"""Typer CLI: mm validate | run | sweep | audit export.

`mm report` is not implemented here yet — it needs the Jinja2/weasyprint
report template, which doesn't exist as of this command's implementation.
"""

from __future__ import annotations

import dataclasses as dc
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pydantic
import typer
import yaml

from rfq_mm.analytics.metrics import (
    compute_fill_spread_stats,
    compute_pnl_attribution,
    intraday_sharpe,
    inventory_path_stats,
)
from rfq_mm.analytics.sweep import SweepPoint, run_sweep, sweep_to_frame
from rfq_mm.compliance.audit import AuditLog
from rfq_mm.compliance.monitor import build_compliance_report
from rfq_mm.core.clock import SessionCalendar
from rfq_mm.engine.quoter import QuoterParams
from rfq_mm.engine.simulation import SimulationParams, run_day_simulation, write_audit_trail
from rfq_mm.feed.bars import BarsFeed
from rfq_mm.feed.base import ReferenceMarketFeed
from rfq_mm.feed.synthetic import SyntheticFeed, SyntheticFeedParams
from rfq_mm.instruments.universe import BY_ISIN, LIVE_SIM_ISINS, START_PRICES_EUR, YFINANCE_TICKERS
from rfq_mm.risk.inventory import InventoryLimits
from rfq_mm.venue.config import VenueConfig, load_venue_config

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _build_sessions(venue_config: VenueConfig) -> tuple[SessionCalendar, SessionCalendar]:
    s = venue_config.session
    venue_session = SessionCalendar.from_primitives(
        s.timezone, s.open, s.close, s.post_trading_minutes, list(s.days)
    )
    rm = venue_config.reference_market
    reference_session = SessionCalendar.from_primitives(
        s.timezone, rm.hours.open, rm.hours.close, 0, list(s.days)
    )
    return venue_session, reference_session


@app.command()
def validate(venue_file: Path) -> None:
    """Load a venue rulebook YAML, validate it, and print the parsed config."""
    try:
        config = load_venue_config(venue_file)
    except pydantic.ValidationError as e:
        typer.secho(f"INVALID venue config: {venue_file}", fg=typer.colors.RED, err=True)
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    except yaml.YAMLError as e:
        typer.secho(f"MALFORMED YAML: {venue_file}", fg=typer.colors.RED, err=True)
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e

    typer.secho(f"OK: {venue_file}", fg=typer.colors.GREEN)
    typer.echo(json.dumps(config.model_dump(mode="json"), indent=2, default=str))


@app.command()
def run(
    venue_file: Path,
    isins: str = typer.Option(
        ",".join(LIVE_SIM_ISINS), help="Comma-separated ISINs to simulate"
    ),
    sim_date: str = typer.Option(..., "--date", help="Simulated day, YYYY-MM-DD"),
    seed: int = typer.Option(42),
    feed_tier: str = typer.Option("synthetic", "--feed", help="synthetic | bars"),
    out_dir: Path = typer.Option(Path("out")),  # noqa: B008
    gamma: float = typer.Option(QuoterParams().gamma),  # noqa: B008
    informed_fraction: float = typer.Option(0.1),
    inventory_limit_eur: float = typer.Option(300_000.0),
) -> None:
    """Run one simulated trading day end-to-end and write fills/quotes/audit
    to --out-dir, printing a compliance + P&L summary."""
    venue_config = load_venue_config(venue_file)
    venue_session, reference_session = _build_sessions(venue_config)
    isin_list = [i.strip() for i in isins.split(",") if i.strip()]
    d = date.fromisoformat(sim_date)

    feed: ReferenceMarketFeed
    if feed_tier == "synthetic":
        start_prices = {isin: START_PRICES_EUR[isin] for isin in isin_list}
        feed = SyntheticFeed(
            venue_session, reference_session, start_prices, SyntheticFeedParams(seed=seed)
        )
    elif feed_tier == "bars":
        if len(isin_list) != 1 or isin_list[0] not in YFINANCE_TICKERS:
            typer.secho(
                "bars feed requires exactly one --isins value with a known yfinance ticker "
                f"(one of {sorted(YFINANCE_TICKERS)})",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        isin = isin_list[0]
        bars_feed = BarsFeed(
            YFINANCE_TICKERS[isin], isin, reference_session, out_dir / "bars_cache"
        )
        available = bars_feed.available_dates
        if d not in available and available:
            d = available[-1]
            typer.echo(f"requested date not in cached bars; using {d.isoformat()} instead")
        feed = bars_feed
    else:
        typer.secho(
            f"unknown --feed {feed_tier!r}, expected synthetic or bars",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    instruments = {isin: BY_ISIN[isin] for isin in isin_list}
    params = SimulationParams(
        seed=seed,
        sim_date=d,
        isins=isin_list,
        quoter_params=QuoterParams(gamma=gamma),
        flow_params_overrides={"informed_fraction": informed_fraction},
        inventory_limits=InventoryLimits(
            max_abs_position_eur=inventory_limit_eur,
            kill_switch_abs_position_eur=inventory_limit_eur * 1.5,
        ),
    )
    result = run_day_simulation(
        venue_config, venue_session, reference_session, feed, instruments, params
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dc.asdict(f) for f in result.fills]).to_parquet(out_dir / "fills.parquet")
    pd.DataFrame([dc.asdict(q) for q in result.quotes]).to_parquet(out_dir / "quotes.parquet")

    audit_log = AuditLog(out_dir / "audit.jsonl")
    write_audit_trail(result, instruments, audit_log)
    audit_log.to_parquet(out_dir / "audit.parquet")
    audit_log.close()

    report = build_compliance_report(
        result.response_tracker,
        result.compliance_log,
        n_accept_attempts=len(result.fills) + len(result.compliance_log.withdrawal_events),
        min_response_ratio=venue_config.obligations.min_response_ratio,
    )

    typer.echo(
        f"RFQs: {len(result.rfqs)}  Quotes: {len(result.quotes)}  Fills: {len(result.fills)}"
    )
    typer.echo(
        f"Response ratio: {report.response_ratio:.4f} (floor {report.response_ratio_floor}) "
        f"presented={report.presented} answered={report.answered}"
    )
    typer.echo(
        f"Clamp events: {report.n_clamp_events}  Withdrawals: {report.n_withdrawals} "
        f"Firm-size breaches: {report.n_firm_size_breaches}"
    )
    if report.is_compliant:
        typer.secho("Compliance: OK", fg=typer.colors.GREEN)
    else:
        typer.secho(f"Compliance: {len(report.breaches)} breach(es)", fg=typer.colors.RED)
        for b in report.breaches:
            typer.echo(f"  - {b.category}: {b.detail}")

    for isin in isin_list:
        isin_fills = [f for f in result.fills if f.isin == isin]
        if not isin_fills:
            continue
        isin_hedges = [h for h in result.hedges if h.isin == isin]
        attribution = compute_pnl_attribution(
            isin_fills, isin_hedges, feed, result.final_units[isin], result.final_mark[isin]
        )
        typer.echo(
            f"{isin}: fills={len(isin_fills)} total_pnl={attribution.total_pnl_eur:,.2f} EUR "
            f"(spread={attribution.spread_capture_eur:,.2f} adverse_selection="
            f"{attribution.adverse_selection_eur:,.2f} inventory_mtm="
            f"{attribution.inventory_mtm_eur:,.2f} hedge_slippage="
            f"{attribution.hedge_slippage_eur:,.2f}) "
            f"reconciliation_error={attribution.reconciliation_error_eur():.4f}"
        )


@app.command()
def sweep(
    venue_file: Path,
    isin: str = typer.Option(LIVE_SIM_ISINS[0]),
    sim_date: str = typer.Option(..., "--date"),
    seed: int = typer.Option(42),
    gammas: str = typer.Option("0.002,0.005,0.01"),
    phis: str = typer.Option("0.0,0.1,0.3"),
    inventory_limits: str = typer.Option("100000,300000"),
    out_file: Path = typer.Option(Path("out/sweep.csv")),  # noqa: B008
) -> None:
    """gamma x phi x inventory-limit sensitivity grid (spec.md §6, R164-R166)."""
    venue_config = load_venue_config(venue_file)
    venue_session, reference_session = _build_sessions(venue_config)
    d = date.fromisoformat(sim_date)
    instruments = {isin: BY_ISIN[isin]}
    start_price = START_PRICES_EUR[isin]

    def run_one(gamma: float, phi: float, inv_limit: float) -> SweepPoint:
        feed = SyntheticFeed(
            venue_session, reference_session, {isin: start_price}, SyntheticFeedParams(seed=seed)
        )
        params = SimulationParams(
            seed=seed,
            sim_date=d,
            isins=[isin],
            quoter_params=QuoterParams(gamma=gamma),
            flow_params_overrides={"informed_fraction": phi},
            inventory_limits=InventoryLimits(
                max_abs_position_eur=inv_limit, kill_switch_abs_position_eur=inv_limit * 1.5
            ),
        )
        result = run_day_simulation(
            venue_config, venue_session, reference_session, feed, instruments, params
        )
        isin_fills = [f for f in result.fills if f.isin == isin]
        attribution = compute_pnl_attribution(
            isin_fills, result.hedges, feed, result.final_units[isin], result.final_mark[isin]
        )
        stats = compute_fill_spread_stats(isin_fills, feed)
        pnl_path = []
        running = 0.0
        for s in stats:
            running += s.effective_spread_eur
            pnl_path.append(running)
        inv_stats = inventory_path_stats(result.inventory_snapshots[isin])
        report = build_compliance_report(
            result.response_tracker,
            result.compliance_log,
            n_accept_attempts=len(result.fills) + len(result.compliance_log.withdrawal_events),
            min_response_ratio=venue_config.obligations.min_response_ratio,
        )
        return SweepPoint(
            gamma=gamma,
            phi=phi,
            inventory_limit_eur=inv_limit,
            pnl_eur=attribution.total_pnl_eur,
            sharpe=intraday_sharpe(pnl_path),
            max_abs_inventory_eur=inv_stats.max_abs_position_eur,
            n_compliance_breaches=len(report.breaches),
        )

    gamma_list = [float(x) for x in gammas.split(",")]
    phi_list = [float(x) for x in phis.split(",")]
    limit_list = [float(x) for x in inventory_limits.split(",")]
    points = run_sweep(gamma_list, phi_list, limit_list, run_one)
    df = sweep_to_frame(points)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_file, index=False)
    typer.echo(df.to_string(index=False))
    typer.echo(f"\nwrote {len(df)} grid points to {out_file}")


audit_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(audit_app, name="audit")


@audit_app.command("export")
def audit_export(
    input_jsonl: Path = typer.Option(..., "--input"),  # noqa: B008
    output_parquet: Path = typer.Option(..., "--output"),  # noqa: B008
) -> None:
    """§16: export a previously-recorded audit trail (JSONL) to Parquet on demand."""
    if not input_jsonl.exists():
        typer.secho(f"no such file: {input_jsonl}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    df = pd.read_json(input_jsonl, lines=True)
    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_parquet)
    typer.echo(f"exported {len(df)} audit records to {output_parquet}")


if __name__ == "__main__":
    app()
