"""Hand-rolled heapq discrete-event queue (spec.md §1: "heapq event queue,
hand-rolled ... do not pull in SimPy"). Shared by flow/generator.py (RFQ
arrivals), engine/rfq.py (TTL expiry, withdrawal deadlines) and
engine/lom.py (§9 price-update rechecks).
"""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(order=True)
class _HeapItem:
    ts: datetime
    seq: int
    event: Any = field(compare=False)


class EventLoop:
    """A min-heap of (timestamp, event) ordered by time, then insertion order.

    The insertion-order tiebreak makes same-timestamp processing
    deterministic and FIFO (R4/R50) rather than dependent on heap internals.
    """

    def __init__(self) -> None:
        self._heap: list[_HeapItem] = []
        self._seq = 0

    def schedule(self, ts: datetime, event: Any) -> None:
        heapq.heappush(self._heap, _HeapItem(ts, self._seq, event))
        self._seq += 1

    def __len__(self) -> int:
        return len(self._heap)

    def is_empty(self) -> bool:
        return not self._heap

    def pop(self) -> tuple[datetime, Any]:
        item = heapq.heappop(self._heap)
        return item.ts, item.event

    def run(self, handler: Callable[[datetime, Any], None]) -> None:
        """Pop events in order until the queue is empty. `handler` may call
        `schedule` to add further events (e.g. a fill scheduling its own
        TTL-expiry check)."""
        while self._heap:
            ts, event = self.pop()
            handler(ts, event)
