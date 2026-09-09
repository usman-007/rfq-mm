"""Typer CLI: mm validate | run | report | sweep | audit export."""

from __future__ import annotations

import json
from pathlib import Path

import pydantic
import typer
import yaml

from rfq_mm.venue.config import load_venue_config

app = typer.Typer(no_args_is_help=True, add_completion=False)


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


if __name__ == "__main__":
    app()
