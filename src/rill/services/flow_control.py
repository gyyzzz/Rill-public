"""Application-level admission control for expensive agent requests."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


class AgentCapacityError(RuntimeError):
    """Raised when a request cannot enter the agent execution queue in time."""


@dataclass
class FlowControlSnapshot:
    active: int
    queued: int
    limit: int


class AgentFlowController:
    """Limit expensive agent executions while allowing short bounded queuing."""

    def __init__(self, max_concurrent: int, queue_timeout: float) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        if queue_timeout < 0:
            raise ValueError("queue_timeout must be non-negative")
        self._limit = max_concurrent
        self._queue_timeout = queue_timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = 0
        self._queued = 0
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._lock:
            self._queued += 1
        queued = True
        try:
            try:
                if self._queue_timeout == 0:
                    if self._semaphore.locked():
                        raise AgentCapacityError("Agent capacity is temporarily exhausted.")
                    await self._semaphore.acquire()
                else:
                    await asyncio.wait_for(self._semaphore.acquire(), timeout=self._queue_timeout)
            except asyncio.TimeoutError as exc:
                raise AgentCapacityError("Agent capacity is temporarily exhausted.") from exc
            async with self._lock:
                self._queued -= 1
                self._active += 1
            queued = False
            try:
                yield
            finally:
                async with self._lock:
                    self._active -= 1
                self._semaphore.release()
        except BaseException:
            # A timed-out/cancelled waiter has not entered the active set.
            if queued:
                async with self._lock:
                    self._queued -= 1
            raise

    async def snapshot(self) -> FlowControlSnapshot:
        async with self._lock:
            return FlowControlSnapshot(self._active, self._queued, self._limit)
