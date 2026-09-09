from rfq_mm.analytics.sweep import SweepPoint, run_sweep, sweep_to_frame


def test_run_sweep_covers_full_grid() -> None:
    calls = []

    def run_one(gamma: float, phi: float, limit: float) -> SweepPoint:
        calls.append((gamma, phi, limit))
        return SweepPoint(
            gamma=gamma,
            phi=phi,
            inventory_limit_eur=limit,
            pnl_eur=gamma * 10,
            sharpe=0.5,
            max_abs_inventory_eur=limit / 2,
            n_compliance_breaches=0,
        )

    points = run_sweep([0.1, 0.2], [0.05, 0.1], [100_000.0], run_one)
    assert len(points) == 4  # 2 x 2 x 1
    assert len(calls) == 4
    assert (0.2, 0.1, 100_000.0) in calls


def test_sweep_to_frame_has_expected_columns() -> None:
    points = [
        SweepPoint(0.1, 0.05, 100_000.0, 500.0, 0.3, 20_000.0, 0),
        SweepPoint(0.2, 0.05, 100_000.0, -200.0, -0.1, 40_000.0, 2),
    ]
    df = sweep_to_frame(points)
    assert list(df.columns) == [
        "gamma",
        "phi",
        "inventory_limit_eur",
        "pnl_eur",
        "sharpe",
        "max_abs_inventory_eur",
        "n_compliance_breaches",
    ]
    assert len(df) == 2
