from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

TERMINAL_TYPES = ("done", "error", "interrupted")


@dataclass
class StreamEvent:
    offset: int
    type: str
    data: dict


class DeltaChannel:
    """In-process publish/subscribe with offset replay buffer (D0 seam).

    Executor publishes; SSE subscribers consume from an offset. Disconnect
    cancels a subscription only, never the generation task (D2.1). Buffers
    live for the process lifetime (Q2 scale); a Redis/PG/DB-poll backend can
    replace this class without touching the executor (2A->2B/2C).
    """

    def __init__(self) -> None:
        self._buffers: dict[str, list[StreamEvent]] = {}
        self._waiters: dict[str, list[asyncio.Event]] = {}
        self._closed: set[str] = set()

    def publish(self, turn_id: str, type: str, data: dict) -> int:
        buf = self._buffers.setdefault(turn_id, [])
        offset = len(buf)
        buf.append(StreamEvent(offset, type, data))
        if type in TERMINAL_TYPES:
            self._closed.add(turn_id)
        for waiter in self._waiters.get(turn_id, ()):
            waiter.set()
        return offset

    async def subscribe(self, turn_id: str, from_offset: int = 0) -> AsyncIterator[StreamEvent]:
        while True:
            buf = self._buffers.get(turn_id, [])
            while from_offset < len(buf):
                event = buf[from_offset]
                from_offset += 1
                yield event
                if event.type in TERMINAL_TYPES:
                    return
            if turn_id in self._closed:
                return
            waiter = asyncio.Event()
            self._waiters.setdefault(turn_id, []).append(waiter)
            try:
                if len(self._buffers.get(turn_id, [])) > from_offset or turn_id in self._closed:
                    waiter.set()
                await waiter.wait()
            finally:
                self._waiters[turn_id].remove(waiter)

    def discard(self, turn_id: str) -> None:
        self._buffers.pop(turn_id, None)
        self._waiters.pop(turn_id, None)
        self._closed.discard(turn_id)
