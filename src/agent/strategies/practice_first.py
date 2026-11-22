from __future__ import annotations

from ..context import ContextBuilder, PlanInput, TurnPlan
from .base import Strategy

INSTRUCTIONS = """你是学习陪伴助手，当前教学策略：练习优先（practice-first）。
教学流程：
1. 对话首轮：不要讲解，先从下方题库选一道题调用 ask_question 出题，探测用户掌握情况（question_id 必须来自题库）。
2. 用户作答后，调用 judge_answer 判定：question_id 为所答题目，user_answer 取用户原话中的答案。
3. 判定为答错：调用 provide_explanation 讲解对应知识点（knowledge_point 用题库标注），指出错因，再出一道同类题巩固。
4. 判定为答对：简短确认，推进到下一个知识点或更难的题目。
5. 需要材料细节时调用 query_material 查询预置材料。
约束：首轮禁止讲解（provide_explanation 本轮不可用）；回复用中文，面向初中学生，简洁清晰。"""

PROBE_TOOLS = ["query_material", "ask_question", "judge_answer"]
FULL_TOOLS = ["query_material", "ask_question", "judge_answer", "provide_explanation"]


class PracticeFirstStrategy(Strategy):
    name = "practice-first"
    version = 1

    def plan(self, plan_input: PlanInput) -> TurnPlan:
        context = ContextBuilder().build(
            plan_input.user_message,
            plan_input.history,
            plan_input.material_candidates,
            plan_input.config,
        )
        first_turn = not plan_input.history
        return TurnPlan(
            instructions=INSTRUCTIONS,
            context=context,
            allowed_tools=list(PROBE_TOOLS if first_turn else FULL_TOOLS),
        )
