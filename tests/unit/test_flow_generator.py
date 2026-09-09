from datetime import date

from rfq_mm.core.clock import SessionCalendar
from rfq_mm.flow.generator import FlowParams, _intensity_per_hour, generate_rfqs

SESSION = SessionCalendar.from_primitives(
    timezone="Europe/Berlin",
    open_time="08:00",
    close_time="22:00",
    post_trading_minutes=5,
    days=["mon", "tue", "wed", "thu", "fri"],
)
DAY = date(2026, 9, 7)


def test_arrival_count_matches_configured_intensity_within_tolerance() -> None:
    params = FlowParams(seed=7)
    session_minutes = (
        SESSION.session_close(DAY) - SESSION.session_open(DAY)
    ).total_seconds() / 60.0
    n = 2000
    total = 0.0
    for i in range(n):
        t = (i + 0.5) / n * session_minutes
        x = t / session_minutes
        total += _intensity_per_hour(x, t, params) / 60.0
    expected = total * (session_minutes / n)

    result = generate_rfqs(SESSION, DAY, ["TESTISIN"], params)
    actual = len(result.rfqs)
    assert abs(actual - expected) / expected < 0.15


def test_deterministic_from_seed() -> None:
    params = FlowParams(seed=123)
    r1 = generate_rfqs(SESSION, DAY, ["A", "B"], params)
    r2 = generate_rfqs(SESSION, DAY, ["A", "B"], params)
    assert [rfq.ts for rfq in r1.rfqs] == [rfq.ts for rfq in r2.rfqs]
    assert [rfq.requester_id for rfq in r1.rfqs] == [rfq.requester_id for rfq in r2.rfqs]


def test_direction_roughly_50_50() -> None:
    params = FlowParams(seed=1, base_lambda_per_hour=200.0)
    result = generate_rfqs(SESSION, DAY, ["A"], params)
    buys = sum(1 for r in result.rfqs if r.side.value == "buy")
    frac = buys / len(result.rfqs)
    assert 0.4 < frac < 0.6


def test_direction_imbalance_configurable() -> None:
    params = FlowParams(seed=1, base_lambda_per_hour=200.0, buy_probability=0.8)
    result = generate_rfqs(SESSION, DAY, ["A"], params)
    buys = sum(1 for r in result.rfqs if r.side.value == "buy")
    frac = buys / len(result.rfqs)
    assert frac > 0.7


def test_size_median_and_fat_tail() -> None:
    params = FlowParams(seed=3, base_lambda_per_hour=300.0, size_median_eur=300.0)
    result = generate_rfqs(SESSION, DAY, ["A"], params)
    sizes = sorted(r.requested_size_eur for r in result.rfqs)
    median = sizes[len(sizes) // 2]
    assert 150 < median < 600
    assert any(s > 10_000 for s in sizes)


def test_informed_fraction_roughly_matches_configured_phi() -> None:
    params = FlowParams(seed=5, base_lambda_per_hour=300.0, informed_fraction=0.2)
    result = generate_rfqs(SESSION, DAY, ["A"], params)
    frac = sum(1 for r in result.rfqs if r.is_informed) / len(result.rfqs)
    assert 0.1 < frac < 0.3


def test_abusers_are_overrepresented_in_arrivals() -> None:
    params = FlowParams(
        seed=9, base_lambda_per_hour=300.0, abuser_fraction=0.02, abuser_rate_multiplier=25.0
    )
    result = generate_rfqs(SESSION, DAY, ["A"], params)
    abuser_hits = sum(1 for r in result.rfqs if r.requester_id in result.abuser_requester_ids)
    share = abuser_hits / len(result.rfqs)
    assert share > 0.2


def test_exemption_not_set_by_generator() -> None:
    params = FlowParams(seed=1, base_lambda_per_hour=100.0)
    result = generate_rfqs(SESSION, DAY, ["A"], params)
    assert all(not r.is_exempt and r.exemption_type is None for r in result.rfqs)
