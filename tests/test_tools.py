"""Tool layer: material query, behavior tools, preset-key judging (B7/D8.4/P1)."""
from __future__ import annotations

import json

from agent.tools import ToolContext, default_tool_registry, normalize_answer


def _ctx(repo):
    return ToolContext(repo=repo, conversation=repo.get_conversation("c-demo"),
                       turn_id="turn-x", owner="inst-A", fence=0)


def test_registry_and_schemas():
    reg = default_tool_registry()
    assert reg.names() == ["ask_question", "judge_answer", "provide_explanation", "query_material"]
    schemas = reg.schemas(["query_material", "ask_question"])
    assert [s["function"]["name"] for s in schemas] == ["query_material", "ask_question"]
    assert schemas[0]["type"] == "function"
    assert "query" in schemas[0]["function"]["parameters"]["properties"]
    assert reg.schemas(["ghost"]) == []


def test_query_material_match_cap_and_empty(repo):
    reg = default_tool_registry()
    tool = reg.get("query_material")
    res = tool.execute({"query": "韦达定理"}, _ctx(repo))
    payload = json.loads(res.content)
    assert len(payload["snippets"]) == 1
    assert payload["snippets"][0]["title"] == "韦达定理"
    assert res.snippet_refs == [payload["snippets"][0]["id"]]

    many = tool.execute({"query": "判别式 求根 韦达 因式分解 配方 常见错误 一般式"}, _ctx(repo))
    assert len(json.loads(many.content)["snippets"]) == 3

    empty = tool.execute({"query": "毫不相关的查询"}, _ctx(repo))
    assert json.loads(empty.content)["snippets"] == []
    assert "note" in json.loads(empty.content)


def test_ask_question_valid_and_invalid(repo):
    tool = default_tool_registry().get("ask_question")
    ok = tool.execute({"question_id": "q-disc-01", "question_text": "Δ 是多少？"}, _ctx(repo))
    payload = json.loads(ok.content)
    assert payload["status"] == "recorded" and payload["question_id"] == "q-disc-01"
    bad = tool.execute({"question_id": "q-ghost", "question_text": "?"}, _ctx(repo))
    payload = json.loads(bad.content)
    assert payload["error"] == "unknown question_id"
    assert "q-disc-01" in payload["available"]


def test_provide_explanation(repo):
    tool = default_tool_registry().get("provide_explanation")
    res = tool.execute({"knowledge_point": "判别式"}, _ctx(repo))
    assert json.loads(res.content) == {"status": "recorded", "knowledge_point": "判别式"}
    assert res.result_ref == {"knowledge_point": "判别式"}


def test_judge_answer_correct_wrong_and_normalization(repo):
    tool = default_tool_registry().get("judge_answer")
    ctx = _ctx(repo)
    for answer in ("1", "Δ=1", " １ ", "Δ = 1", "判别式为1"):
        res = tool.execute({"question_id": "q-disc-01", "user_answer": answer}, ctx)
        assert json.loads(res.content)["correct"] is True, answer
    wrong = tool.execute({"question_id": "q-disc-01", "user_answer": "49"}, ctx)
    payload = json.loads(wrong.content)
    assert payload["correct"] is False
    assert payload["expected"] == "1"
    assert payload["knowledge_point"] == "判别式"
    assert wrong.answer_event["correct"] is False
    assert wrong.answer_event["question_id"] == "q-disc-01"
    assert wrong.answer_event["user_id"] == "u-demo"
    bad = tool.execute({"question_id": "q-ghost", "user_answer": "1"}, ctx)
    assert json.loads(bad.content)["error"] == "unknown question_id"
    assert bad.answer_event is None


def test_normalize_answer():
    assert normalize_answer("  Δ = 1 ") == "δ=1"
    assert normalize_answer("（a≠0）。") == "(a≠0)"
    assert normalize_answer("x²") == "x2"
