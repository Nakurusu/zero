"""Strategy subsystem: registry/versioning (B4/B5), two strategies (B3/B2), fallback (B6/D5.4)."""
from __future__ import annotations

import pytest

from agent.context import ContextBuilder, HistoryTurn, PlanInput, StrategyConfig, TurnPlan
from agent.strategies import (
    DefaultStrategyFailed,
    ExplainFirstStrategy,
    PlanValidationError,
    PracticeFirstStrategy,
    Strategy,
    StrategyRegistry,
    default_registry,
    make_validator,
    plan_with_fallback,
)

TOOLS = ["query_material", "provide_explanation", "ask_question", "judge_answer"]
MATERIALS = [
    {"id": "s1", "title": "判别式", "content": "Δ=b²−4ac", "keywords": ["判别式"]},
    {"id": "s2", "title": "韦达定理", "content": "x₁+x₂=−b/a", "keywords": ["韦达"]},
]


def _input(with_history: bool = False) -> PlanInput:
    history = [HistoryTurn("t0", "u0", "a0", "之前的问", "之前的答")] if with_history else []
    return PlanInput(
        user_message="判别式怎么用",
        history=history,
        learning_digest={"progress": "中等"},
        material_candidates=MATERIALS,
        question_bank=[],
        conversation_meta={"conversation_id": "c1", "topic_id": "t-quadratic", "user_id": "u-demo"},
        config=StrategyConfig(),
    )


def _validator(with_history: bool = False):
    return make_validator(TOOLS, {"u0", "a0"} if with_history else set(), {"s1", "s2"})


class FaultyStrategy(Strategy):
    name = "faulty"
    version = 1

    def plan(self, plan_input):
        raise RuntimeError("boom")


class BadToolStrategy(Strategy):
    name = "bad-tool"
    version = 1

    def plan(self, plan_input):
        ctx = ContextBuilder().build(plan_input.user_message, plan_input.history,
                                     plan_input.material_candidates, plan_input.config)
        return TurnPlan(instructions="x", context=ctx, allowed_tools=["not_registered"])


class SocraticStrategy(Strategy):
    name = "socratic"
    version = 1

    def plan(self, plan_input):
        ctx = ContextBuilder().build(plan_input.user_message, plan_input.history,
                                     plan_input.material_candidates, plan_input.config)
        return TurnPlan(instructions="苏格拉底式追问", context=ctx, allowed_tools=["ask_question"])


def test_registry_defaults_and_versions():
    reg = default_registry()
    assert reg.names() == ["explain-first", "practice-first"]
    s = reg.get("explain-first", 1)
    assert isinstance(s, ExplainFirstStrategy) and s.version == 1
    assert reg.has("practice-first", 1) and not reg.has("practice-first", 2)


def test_register_third_strategy_and_keep_old_version():
    reg = default_registry()
    reg.register("socratic", 1, SocraticStrategy)

    class ExplainV2(ExplainFirstStrategy):
        version = 2

    reg.register("explain-first", 2, ExplainV2)
    assert reg.versions("explain-first") == [1, 2]
    assert reg.get("socratic", 1).plan(_input()).instructions == "苏格拉底式追问"
    assert isinstance(reg.get("explain-first", 1), ExplainFirstStrategy)
    assert reg.get("explain-first", 2).version == 2


def test_explain_first_plan_and_determinism():
    inp = _input()
    p1 = ExplainFirstStrategy().plan(inp)
    p2 = ExplainFirstStrategy().plan(inp)
    assert p1.to_doc() == p2.to_doc()
    assert p1.allowed_tools == TOOLS
    assert "讲解" in p1.instructions
    assert p1.plan_schema_version == 1
    assert p1.context.snippet_ids == ["s1"]


def test_practice_first_tool_gate_by_turn():
    first = PracticeFirstStrategy().plan(_input(with_history=False))
    later = PracticeFirstStrategy().plan(_input(with_history=True))
    assert "provide_explanation" not in first.allowed_tools
    assert "ask_question" in first.allowed_tools
    assert "provide_explanation" in later.allowed_tools


def test_same_pool_same_context_across_strategies():
    inp = _input(with_history=True)
    a = ExplainFirstStrategy().plan(inp)
    b = PracticeFirstStrategy().plan(inp)
    assert a.context.to_dict() == b.context.to_dict()


def test_fallback_on_plan_error():
    reg = default_registry()
    reg.register("faulty", 1, FaultyStrategy)
    out = plan_with_fallback(reg, "faulty", 1, "explain-first", 1, _input(), _validator())
    assert out.actual_name == "explain-first" and out.actual_version == 1
    assert out.fallback_reason == "strategy_plan_error"
    assert out.plan.allowed_tools == TOOLS


def test_fallback_on_validation_error():
    reg = default_registry()
    reg.register("bad-tool", 1, BadToolStrategy)
    out = plan_with_fallback(reg, "bad-tool", 1, "explain-first", 1, _input(), _validator())
    assert out.fallback_reason == "strategy_validation_error"
    assert out.actual_name == "explain-first"


def test_fallback_on_unknown_strategy():
    out = plan_with_fallback(default_registry(), "nope", 9, "explain-first", 1, _input(), _validator())
    assert out.fallback_reason == "strategy_not_found"
    assert out.actual_name == "explain-first"


def test_default_also_fails():
    reg = StrategyRegistry()
    reg.register("faulty", 1, FaultyStrategy)
    reg.register("broken-default", 1, FaultyStrategy)
    with pytest.raises(DefaultStrategyFailed) as ei:
        plan_with_fallback(reg, "faulty", 1, "broken-default", 1, _input(), _validator())
    assert ei.value.original_reason == "strategy_plan_error"


def test_validator_rejects_bad_refs_and_schema():
    inp = _input(with_history=True)
    plan = ExplainFirstStrategy().plan(inp)
    plan.context.message_ids.append("ghost")
    with pytest.raises(PlanValidationError):
        _validator(with_history=True)(plan)
    plan2 = ExplainFirstStrategy().plan(inp)
    plan2.plan_schema_version = 99
    with pytest.raises(PlanValidationError):
        _validator(with_history=True)(plan2)
