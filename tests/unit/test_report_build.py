from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go

from rfq_mm.analytics.metrics import PnLAttribution
from rfq_mm.report.build import (
    _fig_to_base64_png_matplotlib,
    build_inventory_chart,
    build_markout_chart,
    build_pnl_attribution_chart,
    build_quoted_vs_reference_chart,
    build_sweep_heatmap,
    render_desk_report_html,
    render_summary_2page_html,
)

TZ = ZoneInfo("Europe/Berlin")
TS = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)

_ATTRIBUTION = PnLAttribution(
    spread_capture_eur=1_000.0,
    adverse_selection_eur=-50.0,
    inventory_mtm_eur=-200.0,
    hedge_slippage_eur=10.0,
    total_pnl_eur=740.0,
)


def test_build_pnl_attribution_chart_is_a_waterfall_with_correct_values() -> None:
    fig = build_pnl_attribution_chart(_ATTRIBUTION)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.type == "waterfall"
    assert list(trace.y) == [1_000.0, -50.0, -200.0, -10.0, 740.0]
    assert trace.measure[-1] == "total"


def test_build_markout_chart_handles_empty_and_nonempty() -> None:
    empty_fig = build_markout_chart(pd.DataFrame())
    assert isinstance(empty_fig, go.Figure)
    assert len(empty_fig.data) == 0

    df = pd.DataFrame({"horizon_seconds": [60.0, 1.0], "mean": [2.5, 1.0]})
    fig = build_markout_chart(df)
    assert len(fig.data) == 1
    # sorted by horizon_seconds ascending: 1s then 60s
    assert list(fig.data[0].x) == ["1.0s", "60.0s"]


def test_build_quoted_vs_reference_chart_handles_empty_and_nonempty() -> None:
    empty_fig = build_quoted_vs_reference_chart(pd.DataFrame())
    assert len(empty_fig.data) == 0

    df = pd.DataFrame(
        {
            "ts": [TS, TS + timedelta(seconds=1)],
            "quoted_spread_bps": [10.0, 12.0],
            "reference_spread_bps": [20.0, 22.0],
            "cap_bps": [15.0, None],
            "was_clamped": [False, False],
        }
    )
    fig = build_quoted_vs_reference_chart(df)
    # quoted scatter, reference scatter, and cap scatter (one non-null cap row)
    assert len(fig.data) == 3


def test_build_inventory_chart_handles_empty_and_nonempty() -> None:
    empty_fig = build_inventory_chart([])
    assert len(empty_fig.data) == 0

    fig = build_inventory_chart([(TS, 0.0), (TS + timedelta(minutes=1), 500.0)])
    assert len(fig.data) == 1
    assert list(fig.data[0].y) == [0.0, 500.0]


def test_build_sweep_heatmap_handles_empty_and_nonempty() -> None:
    empty_fig = build_sweep_heatmap(pd.DataFrame())
    assert len(empty_fig.data) == 0

    df = pd.DataFrame(
        {
            "gamma": [0.005, 0.005, 0.01, 0.01],
            "phi": [0.0, 0.1, 0.0, 0.1],
            "pnl_eur": [100.0, 90.0, 80.0, 70.0],
        }
    )
    fig = build_sweep_heatmap(df)
    assert len(fig.data) == 1
    assert fig.data[0].type == "heatmap"


def test_matplotlib_fallback_renders_all_supported_trace_types() -> None:
    """The kaleido fallback path (see report/build.py's _fig_to_base64_png
    docstring on kaleido's environment-specific flakiness) has no browser
    dependency, so it can be tested deterministically without kaleido."""
    figures = [
        build_pnl_attribution_chart(_ATTRIBUTION),  # waterfall
        build_markout_chart(pd.DataFrame({"horizon_seconds": [1.0], "mean": [1.0]})),  # bar
        build_inventory_chart([(TS, 0.0), (TS + timedelta(minutes=1), 1.0)]),  # scatter
        build_sweep_heatmap(
            pd.DataFrame({"gamma": [0.005], "phi": [0.0], "pnl_eur": [1.0]})
        ),  # heatmap
    ]
    for fig in figures:
        b64 = _fig_to_base64_png_matplotlib(fig, width=400, height=300)
        assert isinstance(b64, str)
        assert len(b64) > 100
        # a PNG file, base64-decoded, starts with the PNG magic bytes
        import base64

        assert base64.b64decode(b64)[:8] == b"\x89PNG\r\n\x1a\n"


def _context() -> dict:
    return {
        "venue_name": "EIX (European Investor Exchange)",
        "venue_source": "Regelwerk European Investor Exchange, 09.12.2024",
        "sim_date": "2026-09-07",
        "generated_at": "2026-09-07T12:00:00+00:00",
        "response_ratio": 1.0,
        "response_ratio_floor": 0.95,
        "presented": 10,
        "answered": 10,
        "n_clamp_events": 2,
        "n_withdrawals": 0,
        "n_firm_size_breaches": 0,
        "n_halted_rfqs": 0,
        "is_compliant": True,
        "breaches": [],
        "reconciliation_error_eur": 0.0,
        "max_abs_inventory_eur": 1_000.0,
        "time_weighted_inventory_eur": 500.0,
        "hedge_cost_eur": 0.0,
        "fill_ratio_by_bucket": {"0-500": 0.9},
    }


def _figures() -> dict[str, go.Figure]:
    return {
        "pnl": build_pnl_attribution_chart(_ATTRIBUTION),
        "markout": build_markout_chart(pd.DataFrame()),
        "spread": build_quoted_vs_reference_chart(pd.DataFrame()),
        "inventory": build_inventory_chart([]),
    }


def test_render_desk_report_html_interactive_mode_needs_no_kaleido() -> None:
    html = render_desk_report_html(_context(), _figures(), mode="interactive")
    assert "EIX (European Investor Exchange)" in html
    assert "2026-09-07" in html
    assert "plotly" in html.lower()  # Plotly.js loaded via CDN
    assert "<img" not in html  # interactive mode embeds charts as JS, not base64 PNGs


def test_render_summary_2page_html_uses_matplotlib_fallback_chart() -> None:
    markout_fig = build_markout_chart(pd.DataFrame({"horizon_seconds": [1.0], "mean": [1.0]}))
    html = render_summary_2page_html(
        {
            "generated_at": "2026-09-07T12:00:00+00:00",
            "clob_paragraph": "EIX is RFQ-with-reference-market, not a CLOB.",
            "limitations_paragraph": "Every data path is a documented proxy.",
        },
        markout_fig,
    )
    assert "EIX is RFQ-with-reference-market" in html
    assert "data:image/png;base64," in html
