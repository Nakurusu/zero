from __future__ import annotations

import asyncio
import time

from .config import Settings
from .storage.repo import Repo


class ConversationBusy(Exception):
    def __init__(self, conversation_id: str, retry_after: int = 1):
        self.conversation_id = conversation_id
        self.retry_after = retry_after
        super().__init__(f"conversation {conversation_id} has a running turn")


class TurnSlot:
    """Per-conversation whole-turn serialization gate (D3).

    The slot is the partial unique index on turns(conversation_id) WHERE
    status='running'; there is no in-process lock state. Release happens
    implicitly when a turn reaches a terminal state (finalize or reclaim).
    """

    def __init__(self, repo: Repo, settings: Settings):
        self.repo = repo
        self.settings = settings

    async def claim(
        self,
        conv_id: str,
        content: str,
        owner_instance: str,
        planned_strategy: str,
        planned_version: int,
        client_msg_id: str | None = None,
        parent_turn_id: str | None = None,
        eval_run_id: str | None = None,
        case_id: str | None = None,
        turn_index: int | None = None,
    ) -> dict:
        deadline = time.monotonic() + self.settings.slot_wait_timeout
        while True:
            result = self.repo.try_claim(
                conv_id, content, owner_instance, planned_strategy, planned_version,
                client_msg_id=client_msg_id, parent_turn_id=parent_turn_id,
                eval_run_id=eval_run_id, case_id=case_id, turn_index=turn_index,
            )
            if result["kind"] in ("claimed", "exists"):
                return result
            if time.monotonic() >= deadline:
                raise ConversationBusy(conv_id)
            await asyncio.sleep(self.settings.slot_poll_interval)
