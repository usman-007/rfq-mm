"""§3/§9: the Limit Order Management system (engine/lom.py) — the second
execution path alongside the RFQ/market-maker system. See PLAN.md
Decision D14: this was found missing at the S11 spec re-read (R2, R17,
R18, R114n) and built as a dedicated step rather than silently skipped.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from rfq_mm.core.types import BBO, Side
from rfq_mm.engine.lom import (
    LimitOrder,
    LimitOrderBook,
    LimitOrderStatus,
    LomFlowParams,
    generate_limit_orders,
    recheck_tick_times,
)
from rfq_mm.feed.synthetic import SyntheticFeed

TZ = ZoneInfo("Europe/Berlin")
TS = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
ISIN = "DE0007164600"


def _bbo(bid: float, ask: float) -> BBO:
    return BBO(
        ts=TS, isin=ISIN, bid=bid, ask=ask, bid_size_eur=10_000.0, ask_size_eur=10_000.0,
        is_open=True, is_indicative=False,
    )


def test_buy_order_marketable_when_limit_at_or_above_ask() -> None:
    order = LimitOrder(
        order_id="o1", isin=ISIN, requester_id="r1", side=Side.BUY,
        limit_price=100.0, size_eur=1_000.0, ts_submitted=TS,
    )
    assert order.is_marketable(_bbo(bid=99.0, ask=100.0)) is True
    assert order.is_marketable(_bbo(bid=99.0, ask=100.01)) is False


def test_sell_order_marketable_when_limit_at_or_below_bid() -> None:
    order = LimitOrder(
        order_id="o1", isin=ISIN, requester_id="r1", side=Side.SELL,
        limit_price=99.0, size_eur=1_000.0, ts_submitted=TS,
    )
    assert order.is_marketable(_bbo(bid=99.0, ask=100.0)) is True
    assert order.is_marketable(_bbo(bid=98.99, ask=100.0)) is False


def test_recheck_removes_and_returns_only_marketable_orders() -> None:
    book = LimitOrderBook()
    marketable = LimitOrder(
        order_id="hit", isin=ISIN, requester_id="r1", side=Side.BUY,
        limit_price=100.0, size_eur=1_000.0, ts_submitted=TS,
    )
    resting = LimitOrder(
        order_id="miss", isin=ISIN, requester_id="r2", side=Side.BUY,
        limit_price=90.0, size_eur=1_000.0, ts_submitted=TS,
    )
    book.add(marketable)
    book.add(resting)

    triggered = book.recheck(ISIN, _bbo(bid=99.0, ask=100.0))

    assert [o.order_id for o in triggered] == ["hit"]
    assert triggered[0].status == LimitOrderStatus.FILLED
    assert [o.order_id for o in book.resting_orders(ISIN)] == ["miss"]

    # a second recheck at the same price doesn't re-trigger anything
    assert book.recheck(ISIN, _bbo(bid=99.0, ask=100.0)) == []


def test_recheck_on_empty_or_unknown_isin_is_a_noop() -> None:
    book = LimitOrderBook()
    assert book.recheck(ISIN, _bbo(bid=99.0, ask=100.0)) == []


def test_recheck_tick_times_span_the_session_at_the_configured_interval() -> None:
    from rfq_mm.core.clock import SessionCalendar

    session = SessionCalendar.from_primitives(
        "Europe/Berlin", "08:00", "22:00", 5, ["mon", "tue", "wed", "thu", "fri"]
    )
    d = date(2026, 9, 7)
    ticks = recheck_tick_times(session, d, recheck_seconds=60.0)

    assert ticks[0] == session.session_open(d)
    assert ticks[-1] <= session.session_close(d)
    assert ticks[1] - ticks[0] == timedelta(seconds=60.0)


def test_generate_limit_orders_is_deterministic_and_sorted(feed: SyntheticFeed) -> None:
    from rfq_mm.core.clock import SessionCalendar

    session = SessionCalendar.from_primitives(
        "Europe/Berlin", "08:00", "22:00", 5, ["mon", "tue", "wed", "thu", "fri"]
    )
    d = date(2026, 9, 7)
    params = LomFlowParams(seed=42, orders_per_hour=6.0)

    orders_a = generate_limit_orders(session, d, [ISIN], feed, params)
    orders_b = generate_limit_orders(session, d, [ISIN], feed, params)

    assert orders_a  # the default rate over a 14h session produces some orders
    assert [o.order_id for o in orders_a] == [o.order_id for o in orders_b]
    assert [o.limit_price for o in orders_a] == [o.limit_price for o in orders_b]
    assert orders_a == sorted(orders_a, key=lambda o: o.ts_submitted)
    assert all(o.status == LimitOrderStatus.OPEN for o in orders_a)
    assert all(o.size_eur >= params.min_size_eur for o in orders_a)

    # orders rest away from the touch at submission (not already marketable) —
    # the whole point of "resting", see module docstring
    for order in orders_a:
        bbo_at_submission = feed.bbo(order.ts_submitted, ISIN, order.size_eur)
        assert order.is_marketable(bbo_at_submission) is False
