"""Pure planning layer: data contracts + context builder + token estimator (D5/D7).

No IO, clock, randomness or env access anywhere in this module. Everything a
strategy needs arrives via PlanInput; everything it produces is serializable
pure data (TurnPlan), so the executor can render, audit and (later) queue it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

ESTIMATOR_VERSION = "est-v1"
CONTEXT_BUILDER_VERSION = "cb-v1"
PLAN_SCHEMA_VERSION = 1


def estimate_tokens(text: str) -> int:
    """Single shared heuristic estimator (D7.2): CJK ~1 token/char, rest ~4 chars/token.

    Used only for window budgeting; actual metering comes from API usage.
    """
    if not text:
        return 0
    cjk = 0
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            cjk += 1
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def keyword_score(keywords: list[str], title: str | None, text: str) -> int:
    """Shared pure scoring rule for preselect and query_material (D7.3)."""
    score = sum(1 for k in keywords if k and k in text)
    if title and title in text:
        score += 2
    return score


@dataclass(frozen=True)
class StrategyConfig:
    """Narrowed strategy config (D5.1): pool bounds only, never model params."""
    history_token_budget: int = 3000
    history_max_turns: int = 20
    preselect_cap: int = 3


@dataclass
class HistoryTurn:
    turn_id: str
    user_message_id: str
    assistant_message_id: str
    user_content: str
    assistant_content: str

    @classmethod
    def from_pair(cls, turn_id: str, user_msg: dict, assistant_msg: dict) -> HistoryTurn:
        return cls(turn_id, user_msg["id"], assistant_msg["id"],
                   user_msg["content"], assistant_msg["content"])


@dataclass
class PlanInput:
    user_message: str
    history: list[HistoryTurn]
    learning_digest: dict | None
    material_candidates: list[dict]
    question_bank: list[dict]
    conversation_meta: dict
    config: StrategyConfig


@dataclass
class ContextSelection:
    message_ids: list[str]
    snippet_ids: list[str]
    window_params: dict

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TurnPlan:
    instructions: str
    context: ContextSelection
    allowed_tools: list[str]
    plan_schema_version: int = PLAN_SCHEMA_VERSION
    context_builder_version: str = CONTEXT_BUILDER_VERSION

    def to_doc(self) -> dict:
        return asdict(self)


class ContextBuilder:
    """Strategy-called pure helper: selects window + preselected materials (D7).

    Window: token budget with turn-count backstop, turn-atomic (whole
    user/assistant pairs in or out), newest first. Candidates must already be
    completed turns (executor loads them; partial/unfinished never enter).
    Materials: keyword scoring, preselect <= cap; reactive tool fetch is the
    primary path (D7.3).
    """

    version = CONTEXT_BUILDER_VERSION

    def build(
        self,
        user_message: str,
        history: list[HistoryTurn],
        materials: list[dict],
        config: StrategyConfig,
    ) -> ContextSelection:
        selected: list[HistoryTurn] = []
        used = 0
        for turn in reversed(history):
            if len(selected) >= config.history_max_turns:
                break
            cost = estimate_tokens(turn.user_content) + estimate_tokens(turn.assistant_content)
            if used + cost > config.history_token_budget:
                break
            selected.append(turn)
            used += cost
        selected.reverse()
        message_ids = [mid for t in selected for mid in (t.user_message_id, t.assistant_message_id)]

        scored: list[tuple[int, str]] = []
        for m in materials:
            keywords = m["keywords"]
            if isinstance(keywords, str):
                keywords = json.loads(keywords)
            score = keyword_score(keywords, m.get("title"), user_message)
            if score > 0:
                scored.append((-score, m["id"]))
        scored.sort()
        snippet_ids = [sid for _, sid in scored[:config.preselect_cap]]

        return ContextSelection(
            message_ids=message_ids,
            snippet_ids=snippet_ids,
            window_params={
                "token_budget": config.history_token_budget,
                "max_turns": config.history_max_turns,
                "selected_turns": len(selected),
                "estimated_tokens": used,
                "estimator_version": ESTIMATOR_VERSION,
            },
        )
