"""Tool layer (B7/D8.4): material query + structured behavior tools.

Tools read and compute only; all audit writes (tool_calls, answer_events,
turn_context refs) are done by the executor under fencing. judge_answer uses
the preset answer key (P1=A): normalized comparison against the seeded bank.
"""
from __future__ import annotations

import json
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .context import keyword_score
from .storage.repo import Repo


def normalize_answer(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).casefold()
    t = t.replace("\u2212", "-")
    t = "".join(t.split())
    return t.rstrip("。．.")


@dataclass
class ToolContext:
    repo: Repo
    conversation: dict
    turn_id: str
    owner: str
    fence: int


@dataclass
class ToolResult:
    content: str
    result_ref: Any = None
    snippet_refs: list[str] = field(default_factory=list)
    answer_event: dict | None = None


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


class Tool(ABC):
    name: str
    description: str
    parameters: dict

    @abstractmethod
    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        ...

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class QueryMaterialTool(Tool):
    name = "query_material"
    description = "按关键词查询本话题预置学习材料片段，返回最相关的至多 3 条片段内容。"
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "查询关键词"}},
        "required": ["query"],
    }

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        query = str(args.get("query", ""))
        snippets = ctx.repo.get_snippets(ctx.conversation["topic_id"])
        scored = []
        for s in snippets:
            keywords = s["keywords"]
            if isinstance(keywords, str):
                keywords = json.loads(keywords)
            score = keyword_score(keywords, s["title"], query)
            if score > 0:
                scored.append((-score, s["id"], s))
        scored.sort(key=lambda x: (x[0], x[1]))
        top = scored[:3]
        refs = [s["id"] for _, _, s in top]
        payload = {"snippets": [{"id": s["id"], "title": s["title"], "content": s["content"]}
                                for _, _, s in top]}
        if not top:
            payload["note"] = "无匹配片段，可换关键词重试"
        return ToolResult(content=_json(payload), result_ref=refs, snippet_refs=refs)


class AskQuestionTool(Tool):
    name = "ask_question"
    description = "从题库向学生出一道题（标记出题行为）。question_id 必须来自题库。"
    parameters = {
        "type": "object",
        "properties": {
            "question_id": {"type": "string", "description": "题库中的题目 ID"},
            "question_text": {"type": "string", "description": "向学生提出的题目文本"},
        },
        "required": ["question_id", "question_text"],
    }

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        qid = str(args.get("question_id", ""))
        question = ctx.repo.get_question(qid)
        if question is None or question["topic_id"] != ctx.conversation["topic_id"]:
            available = [q["id"] for q in ctx.repo.get_questions(ctx.conversation["topic_id"])]
            return ToolResult(content=_json({"error": "unknown question_id", "available": available}),
                              result_ref={"question_id": qid, "valid": False})
        return ToolResult(
            content=_json({"status": "recorded", "question_id": qid, "prompt": question["prompt"]}),
            result_ref={"question_id": qid, "valid": True},
        )


class ProvideExplanationTool(Tool):
    name = "provide_explanation"
    description = "标记你正在讲解某个知识点（讲解正文写在回复中）。"
    parameters = {
        "type": "object",
        "properties": {"knowledge_point": {"type": "string", "description": "所讲解的知识点名称"}},
        "required": ["knowledge_point"],
    }

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        kp = str(args.get("knowledge_point", ""))
        return ToolResult(content=_json({"status": "recorded", "knowledge_point": kp}),
                          result_ref={"knowledge_point": kp})


class JudgeAnswerTool(Tool):
    name = "judge_answer"
    description = "判定学生对某题的回答是否正确（对照预置答案键），返回答复与期望答案。"
    parameters = {
        "type": "object",
        "properties": {
            "question_id": {"type": "string", "description": "题库中的题目 ID"},
            "user_answer": {"type": "string", "description": "学生原话中的答案"},
        },
        "required": ["question_id", "user_answer"],
    }

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        qid = str(args.get("question_id", ""))
        user_answer = str(args.get("user_answer", ""))
        question = ctx.repo.get_question(qid)
        if question is None or question["topic_id"] != ctx.conversation["topic_id"]:
            return ToolResult(content=_json({"error": "unknown question_id"}),
                              result_ref={"question_id": qid, "valid": False})
        acceptable = question["acceptable"]
        if isinstance(acceptable, str):
            acceptable = json.loads(acceptable)
        norm = normalize_answer(user_answer)
        targets = {normalize_answer(a) for a in acceptable} | {normalize_answer(question["expected"])}
        correct = norm in targets
        conv = ctx.conversation
        return ToolResult(
            content=_json({"correct": correct, "expected": question["expected"],
                           "knowledge_point": question["knowledge_point"]}),
            result_ref={"question_id": qid, "correct": correct},
            answer_event={
                "user_id": conv["user_id"], "topic_id": conv["topic_id"],
                "conv_id": conv["id"], "question_id": qid,
                "user_answer": user_answer, "expected": question["expected"], "correct": correct,
            },
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, allowed: list[str]) -> list[dict]:
        return [self._tools[n].schema() for n in allowed if n in self._tools]


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (QueryMaterialTool(), AskQuestionTool(), ProvideExplanationTool(), JudgeAnswerTool()):
        registry.register(tool)
    return registry
