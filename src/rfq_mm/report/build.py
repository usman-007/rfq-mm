"""Desk report: Jinja2 -> HTML (interactive, Plotly via CDN) and
weasyprint -> PDF (static PNG charts, kaleido) — spec.md §1, §8 deliverable
#2, §9. "Self-contained" (M9 done-when) means every chart is embedded
in the single HTML/PDF file — interactive charts via Plotly's own inline
data, static charts as base64-encoded PNGs — no external file references.
"""

from __future__ import annotations

import base64
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, select_autoescape

from rfq_mm.analytics.metrics import PnLAttribution

TEMPLATES_DIR = Path(__file__).parent / "templates"


def build_pnl_attribution_chart(attribution: PnLAttribution) -> go.Figure:
    labels = ["Spread capture", "Adverse selection", "Inventory MTM", "Hedge slippage", "Total P&L"]
    values = [
        attribution.spread_capture_eur,
        attribution.adverse_selection_eur,
        attribution.inventory_mtm_eur,
        -attribution.hedge_slippage_eur,
        attribution.total_pnl_eur,
    ]
    fig = go.Figure(
        go.Waterfall(
            x=labels,
            y=values,
            measure=["relative", "relative", "relative", "relative", "total"],
        )
    )
    fig.update_layout(title="P&L attribution (EUR)", showlegend=False)
    return fig


def build_markout_chart(markout_summary_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not markout_summary_df.empty:
        df = markout_summary_df.sort_values("horizon_seconds")
        fig.add_bar(x=df["horizon_seconds"].astype(str) + "s", y=df["mean"], name="mean")
    fig.update_layout(
        title="Markout curve (mean, bps, signed by direction)",
        xaxis_title="Horizon after fill",
        yaxis_title="bps",
    )
    return fig


def build_quoted_vs_reference_chart(spread_series_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not spread_series_df.empty:
        df = spread_series_df.sort_values("ts")
        fig.add_scatter(x=df["ts"], y=df["quoted_spread_bps"], name="Quoted spread", mode="markers")
        fig.add_scatter(
            x=df["ts"], y=df["reference_spread_bps"], name="Reference spread", mode="lines"
        )
        cap_df = df.dropna(subset=["cap_bps"])
        if not cap_df.empty:
            fig.add_scatter(
                x=cap_df["ts"], y=cap_df["cap_bps"], name="§7(1) cap", line={"dash": "dot"}
            )
    fig.update_layout(
        title="Quoted vs. reference spread (bps)", xaxis_title="Time", yaxis_title="bps"
    )
    return fig


def build_inventory_chart(snapshots: list[tuple[Any, float]]) -> go.Figure:
    fig = go.Figure()
    if snapshots:
        ts = [s[0] for s in snapshots]
        pos = [s[1] for s in snapshots]
        fig.add_scatter(x=ts, y=pos, mode="lines", name="Inventory (EUR)")
    fig.update_layout(title="Inventory path (EUR)", xaxis_title="Time", yaxis_title="EUR")
    return fig


def build_sweep_heatmap(sweep_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not sweep_df.empty:
        pivot = sweep_df.pivot_table(index="gamma", columns="phi", values="pnl_eur", aggfunc="mean")
        fig.add_heatmap(z=pivot.values, x=pivot.columns, y=pivot.index, colorscale="RdBu")
    fig.update_layout(
        title="Sensitivity: P&L by gamma x phi", xaxis_title="phi", yaxis_title="gamma"
    )
    return fig


_KALEIDO_WORKER_SCRIPT = """\
import base64, sys
import plotly.io as pio
fig = pio.from_json(sys.stdin.read())
width, height = int(sys.argv[1]), int(sys.argv[2])
png_bytes = fig.to_image(format="png", width=width, height=height)
sys.stdout.write(base64.b64encode(png_bytes).decode("ascii"))
"""


def _fig_to_base64_png_matplotlib(fig: go.Figure, width: int, height: int) -> str:
    """Fallback static renderer, used only when kaleido fails (see
    _fig_to_base64_png). Re-plots the figure's traces with matplotlib,
    which has no browser/subprocess dependency at all. Covers exactly the
    trace types this module's chart builders actually produce (bar,
    scatter, waterfall, heatmap) — not a general Plotly-to-matplotlib
    converter."""
    import io

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dpi = 100
    mpl_fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    for trace in fig.data:
        ttype = trace.type
        if ttype == "bar":
            ax.bar([str(v) for v in trace.x], trace.y, label=trace.name)
        elif ttype == "waterfall":
            x_labels = [str(v) for v in trace.x]
            values = list(trace.y)
            bottoms = []
            running = 0.0
            for v, measure in zip(
                values, trace.measure or ["relative"] * len(values), strict=False
            ):
                if measure == "total":
                    bottoms.append(0.0)
                else:
                    bottoms.append(running)
                    running += v
            ax.bar(x_labels, values, bottom=bottoms)
        elif ttype == "scatter":
            mode = trace.mode or "lines"
            linestyle = "-" if "lines" in mode else "None"
            marker = "o" if "markers" in mode else None
            ax.plot(trace.x, trace.y, linestyle=linestyle, marker=marker, label=trace.name)
        elif ttype == "heatmap":
            im = ax.imshow(trace.z, aspect="auto", cmap="RdBu")
            mpl_fig.colorbar(im, ax=ax)
            if trace.x is not None:
                ax.set_xticks(range(len(trace.x)))
                ax.set_xticklabels([str(v) for v in trace.x])
            if trace.y is not None:
                ax.set_yticks(range(len(trace.y)))
                ax.set_yticklabels([str(v) for v in trace.y])

    layout = fig.layout
    if layout.title and layout.title.text:
        ax.set_title(layout.title.text)
    if layout.xaxis and layout.xaxis.title and layout.xaxis.title.text:
        ax.set_xlabel(layout.xaxis.title.text)
    if layout.yaxis and layout.yaxis.title and layout.yaxis.title.text:
        ax.set_ylabel(layout.yaxis.title.text)
    if any(trace.name for trace in fig.data):
        ax.legend(fontsize="small")
    mpl_fig.autofmt_xdate()
    mpl_fig.tight_layout()

    buf = io.BytesIO()
    mpl_fig.savefig(buf, format="png")
    plt.close(mpl_fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _fig_to_base64_png(fig: go.Figure, width: int = 900, height: int = 480) -> str:
    # plotly>=6 / kaleido>=1 launches a headless Chrome per export via
    # choreographer's async pipe protocol, and that launch is observably
    # flaky in this project's dev environment — the same call sometimes
    # fails and sometimes succeeds with nothing else different, including
    # when run as the only thing a fresh `python3` process does. Isolating
    # each export in its own subprocess (rather than calling to_image()
    # in-process) measurably reduces how often this shows up, and a short
    # retry covers some of the rest. If kaleido still fails after retrying,
    # fall back to a matplotlib rendering of the same figure rather than
    # leaving the report without this chart — matplotlib has no browser
    # dependency at all, at the cost of losing Plotly's exact styling.
    # fig.to_json()/pio.from_json() (not to_plotly_json()+json.dumps, which
    # can't serialize the numpy arrays and pandas Timestamps these charts
    # actually contain) round-trip through Plotly's own JSON encoder to
    # hand the figure to the subprocess.
    fig_json = fig.to_json()
    last_stderr = ""
    for attempt in range(3):
        result = subprocess.run(
            [sys.executable, "-c", _KALEIDO_WORKER_SCRIPT, str(width), str(height)],
            input=fig_json,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return str(result.stdout)
        last_stderr = result.stderr
        time.sleep(1.0 * (attempt + 1))
    print(
        f"warning: kaleido PNG export failed 3x, falling back to matplotlib "
        f"for this chart (see README):\n{last_stderr}",
        file=sys.stderr,
    )
    return _fig_to_base64_png_matplotlib(fig, width, height)


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html", "j2"])
    )


def render_desk_report_html(
    context: dict[str, Any], figures: dict[str, go.Figure], *, mode: str
) -> str:
    """mode: "interactive" (Plotly via CDN, for on-screen viewing) or
    "static" (base64 PNGs via kaleido, for weasyprint -> PDF, which can't
    execute the JS an interactive Plotly chart needs)."""
    template = _env().get_template("desk_report.html.j2")
    if mode == "interactive":
        chart_html = {}
        include_js = True
        for name, fig in figures.items():
            chart_html[name] = fig.to_html(
                full_html=False, include_plotlyjs=("cdn" if include_js else False)
            )
            include_js = False
        return template.render(mode="interactive", charts=chart_html, **context)
    chart_images = {name: _fig_to_base64_png(fig) for name, fig in figures.items()}
    return template.render(mode="static", chart_images=chart_images, **context)


def render_summary_2page_html(context: dict[str, Any], markout_fig: go.Figure) -> str:
    template = _env().get_template("summary_2page.html.j2")
    return template.render(
        chart_image=_fig_to_base64_png(markout_fig, width=800, height=350), **context
    )


def html_to_pdf(html: str, output_path: Path) -> None:
    from weasyprint import HTML

    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html).write_pdf(str(output_path))
