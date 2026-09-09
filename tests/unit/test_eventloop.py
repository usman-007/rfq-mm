from datetime import datetime, timedelta

from rfq_mm.core.eventloop import EventLoop


def test_events_processed_in_time_order() -> None:
    loop = EventLoop()
    base = datetime(2026, 9, 7, 8, 0)
    loop.schedule(base + timedelta(seconds=5), "c")
    loop.schedule(base + timedelta(seconds=1), "a")
    loop.schedule(base + timedelta(seconds=3), "b")

    order = []
    loop.run(lambda ts, event: order.append(event))
    assert order == ["a", "b", "c"]


def test_same_timestamp_is_fifo() -> None:
    loop = EventLoop()
    ts = datetime(2026, 9, 7, 8, 0)
    for i in range(5):
        loop.schedule(ts, i)

    order = []
    loop.run(lambda ts, event: order.append(event))
    assert order == [0, 1, 2, 3, 4]


def test_handler_can_schedule_further_events() -> None:
    loop = EventLoop()
    base = datetime(2026, 9, 7, 8, 0)
    loop.schedule(base, 0)

    order = []

    def handler(ts: datetime, event: int) -> None:
        order.append(event)
        if event < 3:
            loop.schedule(ts + timedelta(seconds=1), event + 1)

    loop.run(handler)
    assert order == [0, 1, 2, 3]
