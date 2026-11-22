"""Context builder + estimator: pure-module checks for B8/D7/E2."""
from __future__ import annotations

import json

from agent.context import (
    CONTEXT_BUILDER_VERSION,
    ESTIMATOR_VERSION,
    ContextBuilder,
    HistoryTurn,
    StrategyConfig,
    TurnPlan,
    estimate_tokens,
)
from agent.storage import Repo


def _turn(i: int, user: str = "用户消息内容测试", assistant: str = "回复内容测试啊") -> HistoryTurn:
    return HistoryTurn(f"t{i}", f"u{i}", f"a{i}", user, assistant)


def test_estimator():
    assert estimate_tokens("") == 0
    assert estimate_tokens("判别式") == 3
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("判别式abcd") == 4
    assert ESTIMATOR_VERSION == "est-v1"


def test_window_budget_turn_atomic_and_order():
    history = [_turn(i) for i in range(30)]
    config = StrategyConfig(history_token_budget=50, history_max_turns=20)
    sel = ContextBuilder().build("新问题", history, [], config)
    assert sel.window_params["selected_turns"] == 3
    assert sel.window_params["estimated_tokens"] == 45
    assert sel.message_ids == ["u27", "a27", "u28", "a28", "u29", "a29"]
    assert sel.window_params["estimator_version"] == ESTIMATOR_VERSION


def test_window_count_backstop():
    history = [_turn(i) for i in range(10)]
    config = StrategyConfig(history_token_budget=100000, history_max_turns=2)
    sel = ContextBuilder().build("新问题", history, [], config)
    assert sel.message_ids == ["u8", "a8", "u9", "a9"]


def test_window_oversized_newest_turn_excluded():
    history = [_turn(0), _turn(1, user="超" * 200, assistant="长" * 200)]
    config = StrategyConfig(history_token_budget=50, history_max_turns=20)
    sel = ContextBuilder().build("新问题", history, [], config)
    assert sel.message_ids == []
    assert sel.window_params["selected_turns"] == 0


def test_preselect_cap_and_determinism():
    materials = [
        {"id": f"s{i}", "title": f"片段{i}", "content": "x", "keywords": ["韦达"]}
        for i in range(5)
    ]
    config = StrategyConfig(preselect_cap=3)
    sel = ContextBuilder().build("韦达定理怎么用", [], materials, config)
    assert sel.snippet_ids == ["s0", "s1", "s2"]
    again = ContextBuilder().build("韦达定理怎么用", [], materials, config)
    assert again.snippet_ids == sel.snippet_ids
    none = ContextBuilder().build("无关问题", [], materials, config)
    assert none.snippet_ids == []


def test_preselect_keywords_json_string_and_title_boost():
    materials = [
        {"id": "sA", "title": "判别式", "content": "x", "keywords": json.dumps(["判别式", "delta"])},
        {"id": "sB", "title": "韦达定理", "content": "x", "keywords": json.dumps(["韦达"])},
    ]
    sel = ContextBuilder().build("判别式是什么", [], materials, StrategyConfig())
    assert sel.snippet_ids == ["sA"]


def test_history_excludes_unfinished_turns(db_path):
    repo = Repo(db_path)
    repo.heartbeat("inst-live")
    cid2 = repo.create_conversation("t-quadratic", "u-demo", "explain-first", 1)["id"]
    t1 = repo.try_claim("c-demo", "完成轮", "inst-live", "explain-first", 1)["turn"]["id"]
    repo.finalize_turn(t1, "inst-live", 0, status="completed", content="完整回复")
    t2 = repo.try_claim(cid2, "中断轮", "inst-dead", "explain-first", 1)["turn"]["id"]
    repo.flush_partial(t2, "inst-dead", 0, "半截")
    repo.reclaim_stale(5.0)
    hist = repo.history_turns("c-demo")
    assert len(hist) == 1 and hist[0]["turn"]["id"] == t1
    assert all(m["is_partial"] == 0 for m in hist[0]["messages"])
    assert repo.history_turns(cid2) == []
    repo.close()


def test_turn_plan_serializable():
    sel = ContextBuilder().build("韦达定理", [_turn(0)], [], StrategyConfig())
    plan = TurnPlan(instructions="先讲解", context=sel, allowed_tools=["query_material"])
    doc = plan.to_doc()
    roundtrip = json.loads(json.dumps(doc, ensure_ascii=False))
    assert roundtrip == doc
    assert roundtrip["plan_schema_version"] == 1
    assert roundtrip["context_builder_version"] == CONTEXT_BUILDER_VERSION
    assert roundtrip["context"]["message_ids"] == ["u0", "a0"]
