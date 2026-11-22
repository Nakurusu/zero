from __future__ import annotations

from ..context import ContextBuilder, PlanInput, TurnPlan
from .base import Strategy

INSTRUCTIONS = """你是学习陪伴助手，当前教学策略：讲解优先（explain-first）。
教学流程：
1. 用户提出学习需求或新知识点时，先讲解相关知识，再安排练习。
2. 开始讲解时，调用 provide_explanation，参数 knowledge_point 为所讲知识点名称；讲解正文写在回复里，可引用材料。
3. 讲解完成后，从下方题库选择一道合适的题，调用 ask_question 出题：question_id 必须来自题库，question_text 用题库原题或等价改写。
4. 用户作答后，调用 judge_answer 判定：question_id 为所答题目，user_answer 取用户原话中的答案；答错则讲解错因，答对则确认并推进。
5. 需要材料细节时调用 query_material 查询预置材料。
约束：讲解必须先于出题；不要一次性讲完所有知识点；回复用中文，面向初中学生，简洁清晰。"""

ALLOWED_TOOLS = ["query_material", "provide_explanation", "ask_question", "judge_answer"]


class ExplainFirstStrategy(Strategy):
    name = "explain-first"
    version = 1

    def plan(self, plan_input: PlanInput) -> TurnPlan:
        context = ContextBuilder().build(
            plan_input.user_message,
            plan_input.history,
            plan_input.material_candidates,
            plan_input.config,
        )
        return TurnPlan(
            instructions=INSTRUCTIONS,
            context=context,
            allowed_tools=list(ALLOWED_TOOLS),
        )
