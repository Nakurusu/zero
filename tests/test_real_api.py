"""Real-API acceptance: T1 true restart, T2 disconnect, T4 switch/fallback/extension, cancel.

All model generation uses the real API (assignment requirement). Servers run
as uvicorn subprocesses against temp seeded DBs; T1 kills and restarts the
actual service process.
"""
from __future__ import annotations

import time

import httpx
import pytest

from tests.server import (
    LONG_PROMPT,
    Server,
    create_conversation,
    requires_api,
    run_stream,
    seed_db,
    sse_events,
    wait_turn,
)

pytestmark = [requires_api, pytest.mark.real_api]


def _hist(base_url, cid):
    r = httpx.get(f"{base_url}/conversations/{cid}/messages", timeout=10)
    assert r.status_code == 200
    return r.json()


def _turn(hist, tid):
    return next(t for t in hist["turns"] if t["turn_id"] == tid)


def _assistant(hist, tid):
    return next(m for m in hist["messages"] if m["turn_id"] == tid and m["role"] == "assistant")


def test_t1_restart_persistence_and_orphan(real_server):
    s = real_server
    cid = create_conversation(s.base_url)
    evs, tid = run_stream(s.base_url, cid, "用一句话说明判别式的作用。", "t1-done")
    assert evs[-1][0] == "done", evs[-1]

    s.kill()
    s.start()
    hist = _hist(s.base_url, cid)
    assert _turn(hist, tid)["status"] == "completed"
    assert _assistant(hist, tid)["is_partial"] is False
    assert len(_assistant(hist, tid)["content"]) > 0

    evs2, tid2 = run_stream(s.base_url, cid, LONG_PROMPT, "t1-orphan",
                            mid_delta=lambda t: s.kill())
    assert evs2[-1][0] == "exception"
    s.start()
    time.sleep(3.5)  # heartbeat TTL=2 + reaper period
    hist2 = _hist(s.base_url, cid)
    tb2 = _turn(hist2, tid2)
    assert tb2["status"] == "interrupted" and tb2["interrupt_reason"] == "process_restart"
    assert _assistant(hist2, tid2)["is_partial"] is True
    assert _turn(hist2, tid)["status"] == "completed"
    assert len(hist2["turns"]) == 2


def test_t2_disconnect_background_continue(real_server):
    s = real_server
    cid = create_conversation(s.base_url)
    tid = None
    with httpx.Client(timeout=180) as c:
        with c.stream("POST", f"{s.base_url}/conversations/{cid}/messages",
                      json={"content": "请讲解韦达定理并给出推导。", "client_msg_id": "t2-disc"}) as r:
            assert r.status_code == 200
            for et, data in sse_events(r):
                if et == "turn_started":
                    tid = data["turn_id"]
                if et == "delta":
                    break  # abrupt disconnect mid-stream
    assert tid
    tb = wait_turn(s.base_url, cid, tid)
    assert tb["status"] == "completed", tb
    assistant = _assistant(_hist(s.base_url, cid), tid)
    assert assistant["is_partial"] is False
    assert len(assistant["content"]) > 20


def test_t4_fallback_switch_and_third_strategy(faulty_server):
    s = faulty_server
    cid = create_conversation(s.base_url, strategy="faulty")
    evs, tid = run_stream(s.base_url, cid, "判别式是什么？", "t4-fb")
    assert evs[-1][0] == "done", evs[-1]
    tb = _turn(_hist(s.base_url, cid), tid)
    assert tb["planned_strategy"] == "faulty" and tb["planned_version"] == 1
    assert tb["actual_strategy"] == "explain-first" and tb["actual_version"] == 1
    assert tb["fallback_reason"] == "strategy_plan_error"
    audit = httpx.get(f"{s.base_url}/conversations/{cid}/turns/{tid}", timeout=10).json()
    assert "讲解优先" in audit["plan_doc"]["instructions"]

    assert httpx.patch(f"{s.base_url}/conversations/{cid}/strategy",
                       json={"strategy_name": "practice-first", "strategy_version": 1},
                       timeout=10).status_code == 200
    evs2, tid2 = run_stream(s.base_url, cid, "我想复习一元二次方程。", "t4-switch")
    assert evs2[-1][0] == "done"
    tb2 = _turn(_hist(s.base_url, cid), tid2)
    assert tb2["actual_strategy"] == "practice-first" and tb2["fallback_reason"] is None

    assert httpx.patch(f"{s.base_url}/conversations/{cid}/strategy",
                       json={"strategy_name": "socratic", "strategy_version": 1},
                       timeout=10).status_code == 200
    evs3, tid3 = run_stream(s.base_url, cid, "继续，换个方式引导我。", "t4-third")
    assert evs3[-1][0] == "done"
    tb3 = _turn(_hist(s.base_url, cid), tid3)
    assert tb3["actual_strategy"] == "socratic"
    audit3 = httpx.get(f"{s.base_url}/conversations/{cid}/turns/{tid3}", timeout=10).json()
    assert all(t["was_allowed"] for t in audit3["tool_calls"])
    assert {t["tool_name"] for t in audit3["tool_calls"]} <= {"ask_question", "query_material"}


def test_t4_default_also_fails(tmp_path):
    s = Server(seed_db(tmp_path / "broken.db"), app="tests.faulty_app:app",
               extra_env={"AGENT_DEFAULT_STRATEGY": "broken-default",
                          "AGENT_DEFAULT_STRATEGY_VERSION": "1"}).start()
    try:
        cid = create_conversation(s.base_url, strategy="faulty")
        evs, tid = run_stream(s.base_url, cid, "判别式是什么？", "t4-double")
        assert evs[-1][0] == "error"
        assert evs[-1][1]["error_code"] == "strategy_plan_failed"
        hist = _hist(s.base_url, cid)
        tb = _turn(hist, tid)
        assert tb["status"] == "failed"
        assert tb["fallback_reason"] == "strategy_plan_error"
        assert tb["actual_strategy"] is None
        user = next(m for m in hist["messages"] if m["turn_id"] == tid and m["role"] == "user")
        assert user["content"] == "判别式是什么？"
        assert _assistant(hist, tid)["is_partial"] is True
    finally:
        s.stop()


def test_cancel_mid_stream_and_slot_release(real_server):
    s = real_server
    cid = create_conversation(s.base_url)
    evs, tid = run_stream(
        s.base_url, cid, LONG_PROMPT, "cancel-1",
        mid_delta=lambda t: httpx.post(f"{s.base_url}/conversations/{cid}/turns/{t}/cancel", timeout=10),
    )
    assert evs[-1][0] == "interrupted" and evs[-1][1]["reason"] == "user_cancel"
    assert _assistant(_hist(s.base_url, cid), tid)["is_partial"] is True
    rc = httpx.post(f"{s.base_url}/conversations/{cid}/turns/{tid}/cancel", timeout=10)
    assert rc.json()["status"] == "interrupted"  # terminal no-op
    evs2, tid2 = run_stream(s.base_url, cid, "判别式的符号是什么？", "cancel-2")
    assert evs2[-1][0] == "done"
    assert _turn(_hist(s.base_url, cid), tid2)["turn_seq"] == 2
