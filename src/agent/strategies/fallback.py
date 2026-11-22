from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..context import PLAN_SCHEMA_VERSION, PlanInput, TurnPlan
from .registry import StrategyNotFound, StrategyRegistry


class PlanValidationError(Exception):
    pass


class DefaultStrategyFailed(Exception):
    def __init__(self, original_reason: str):
        self.original_reason = original_reason
        super().__init__(f"default strategy also failed (original: {original_reason})")


@dataclass
class PlanOutcome:
    plan: TurnPlan
    actual_name: str
    actual_version: int
    fallback_reason: str | None


def make_validator(
    registered_tools: list[str],
    history_message_ids: set[str],
    snippet_ids: set[str],
) -> Callable[[TurnPlan], None]:
    """Executor-side plan validation (D5.2): tools registered, refs valid, schema compatible."""
    tool_set = set(registered_tools)

    def validate(plan: TurnPlan) -> None:
        if plan.plan_schema_version != PLAN_SCHEMA_VERSION:
            raise PlanValidationError(f"unsupported plan_schema_version {plan.plan_schema_version}")
        unknown = set(plan.allowed_tools) - tool_set
        if unknown:
            raise PlanValidationError(f"unregistered tools: {sorted(unknown)}")
        bad_msgs = set(plan.context.message_ids) - history_message_ids
        if bad_msgs:
            raise PlanValidationError(f"invalid message refs: {sorted(bad_msgs)}")
        bad_snips = set(plan.context.snippet_ids) - snippet_ids
        if bad_snips:
            raise PlanValidationError(f"invalid snippet refs: {sorted(bad_snips)}")

    return validate


def plan_with_fallback(
    registry: StrategyRegistry,
    planned_name: str,
    planned_version: int,
    default_name: str,
    default_version: int,
    plan_input: PlanInput,
    validator: Callable[[TurnPlan], None],
) -> PlanOutcome:
    """Fallback covers ONLY plan construction + validation (D5.4/B6).

    Executor-stage failures (model/tool) never trigger strategy fallback.
    """
    try:
        plan = registry.get(planned_name, planned_version).plan(plan_input)
        validator(plan)
        return PlanOutcome(plan, planned_name, planned_version, None)
    except Exception as exc:
        if isinstance(exc, PlanValidationError):
            reason = "strategy_validation_error"
        elif isinstance(exc, StrategyNotFound):
            reason = "strategy_not_found"
        else:
            reason = "strategy_plan_error"
        try:
            plan = registry.get(default_name, default_version).plan(plan_input)
            validator(plan)
            return PlanOutcome(plan, default_name, default_version, reason)
        except Exception:
            raise DefaultStrategyFailed(reason) from exc
