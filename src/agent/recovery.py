from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .storage.repo import Repo

logger = logging.getLogger("agent.recovery")


class TaskRegistry:
    """Tracks detached generation tasks so shutdown can drain them (D0 3A)."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, deadline: float) -> tuple[set[asyncio.Task], set[asyncio.Task]]:
        if not self._tasks:
            return set(), set()
        return await asyncio.wait(set(self._tasks), timeout=deadline)


class Recovery:
    """Owner-aware orphan reclaim via instance UUID + heartbeat freshness (D1.5/D8.7).

    Full restart: every old instance's heartbeat goes stale -> all reclaimed.
    Rolling restart: only the restarted instance goes stale -> siblings untouched.
    """

    def __init__(self, repo: Repo, settings: Settings, instance_uuid: str):
        self.repo = repo
        self.settings = settings
        self.instance_uuid = instance_uuid

    def reclaim_now(self, reason: str = "process_restart") -> list[str]:
        ids = self.repo.reclaim_stale(self.settings.heartbeat_ttl, reason)
        for turn_id in ids:
            logger.info("reclaimed orphan turn_id=%s reason=%s", turn_id, reason)
        return ids

    async def heartbeat_loop(self) -> None:
        while True:
            self.repo.heartbeat(self.instance_uuid)
            await asyncio.sleep(self.settings.heartbeat_interval)

    async def reaper_loop(self) -> None:
        while True:
            self.reclaim_now()
            await asyncio.sleep(self.settings.heartbeat_ttl)
