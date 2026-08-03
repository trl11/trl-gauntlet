"""Per-run event bus.

The supervisor publishes; SSE clients subscribe. A bounded ring holds recent
events, replayed to a subscriber before the live tail.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    """One published event."""

    seq: int
    type: str = "log"
    ts: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "type": self.type, **self.payload}


class EventBus:
    """Bounded history plus asyncio fan-out."""

    def __init__(self, *, capacity: int = 5000, queue_size: int = 1024) -> None:
        self._buf: deque[Event] = deque(maxlen=capacity)
        self._seq = 0
        self._subscribers: list[asyncio.Queue[Event | None]] = []
        self._queue_size = queue_size
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    def history(self, since: int = 0) -> list[Event]:
        """Events newer than ``since``."""
        return [e for e in self._buf if e.seq > since]

    async def publish(self, event_type: str, **payload: Any) -> Event:
        """Publish from the event loop."""
        async with self._lock:
            return self._append(event_type, payload)

    def publish_threadsafe(self, event_type: str, **payload: Any) -> Event:
        """Publish from a reader thread, without touching the event loop."""
        return self._append(event_type, payload)

    async def subscribe(self, since: int = 0) -> tuple[asyncio.Queue[Event | None], list[Event]]:
        """Subscribe and receive the replay atomically."""
        async with self._lock:
            replay = [e for e in self._buf if e.seq > since]
            queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=self._queue_size)
            self._subscribers.append(queue)
        return queue, replay

    def unsubscribe(self, queue: asyncio.Queue[Event | None]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def close(self) -> None:
        """Mark the bus closed and wake every subscriber with a sentinel."""
        self._closed = True
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    def _append(self, event_type: str, payload: dict[str, Any]) -> Event:
        self._seq += 1
        event = Event(seq=self._seq, type=event_type, payload=payload)
        self._buf.append(event)
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        return event
