from __future__ import annotations

from typing import Callable

from .base import Strategy


class StrategyNotFound(KeyError):
    pass


class StrategyRegistry:
    """name -> {version -> factory} (D6). Old versions stay registered for rollback.

    Adding a strategy = implement Strategy + one register() call; storage,
    HTTP and the executor never change (B4).
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[int, Callable[[], Strategy]]] = {}

    def register(self, name: str, version: int, factory: Callable[[], Strategy]) -> None:
        self._entries.setdefault(name, {})[version] = factory

    def get(self, name: str, version: int) -> Strategy:
        try:
            factory = self._entries[name][version]
        except KeyError:
            raise StrategyNotFound(f"{name}@{version}") from None
        return factory()

    def has(self, name: str, version: int) -> bool:
        return version in self._entries.get(name, {})

    def versions(self, name: str) -> list[int]:
        return sorted(self._entries.get(name, {}))

    def names(self) -> list[str]:
        return sorted(self._entries)


def default_registry() -> StrategyRegistry:
    from .explain_first import ExplainFirstStrategy
    from .practice_first import PracticeFirstStrategy

    registry = StrategyRegistry()
    registry.register(ExplainFirstStrategy.name, ExplainFirstStrategy.version, ExplainFirstStrategy)
    registry.register(PracticeFirstStrategy.name, PracticeFirstStrategy.version, PracticeFirstStrategy)
    return registry
