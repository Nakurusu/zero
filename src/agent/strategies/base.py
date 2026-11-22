from __future__ import annotations

from abc import ABC, abstractmethod

from ..context import PlanInput, TurnPlan


class Strategy(ABC):
    """Pure planner (D5): no IO, clock, randomness or env access.

    Decides instructions, context selection and allowed tools for one turn.
    Never calls the model, executes tools or touches storage.
    """

    name: str
    version: int

    @abstractmethod
    def plan(self, plan_input: PlanInput) -> TurnPlan:
        ...
