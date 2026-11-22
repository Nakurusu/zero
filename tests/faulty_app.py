"""Fault-injection app for T4: extra strategies registered via the public registry API only.

No changes to storage, HTTP or executor — this file is the B4 extension proof.
"""
from agent.api import create_app
from agent.config import Settings
from agent.context import ContextBuilder, TurnPlan
from agent.strategies import Strategy, default_registry


class FaultyStrategy(Strategy):
    name = "faulty"
    version = 1

    def plan(self, plan_input):
        raise RuntimeError("injected plan failure")


class BrokenDefaultStrategy(Strategy):
    name = "broken-default"
    version = 1

    def plan(self, plan_input):
        raise RuntimeError("injected default failure")


class SocraticStrategy(Strategy):
    name = "socratic"
    version = 1

    def plan(self, plan_input):
        context = ContextBuilder().build(
            plan_input.user_message, plan_input.history,
            plan_input.material_candidates, plan_input.config,
        )
        return TurnPlan(
            instructions="你是学习陪伴助手，当前策略：苏格拉底式追问。不要直接讲解，"
                         "用 ask_question 从题库出题引导学生自己思考；可用 query_material 查材料。回复中文简洁。",
            context=context,
            allowed_tools=["ask_question", "query_material"],
        )


registry = default_registry()
registry.register("faulty", 1, FaultyStrategy)
registry.register("broken-default", 1, BrokenDefaultStrategy)
registry.register("socratic", 1, SocraticStrategy)

app = create_app(Settings.from_env(), strategies=registry)
