"""Storage-layer acceptance: T3 concurrency, idempotency, fencing, recovery.

T3 is verified at the storage layer with two processes AND two independent
DB connections, as the assignment allows. T1 (real restart) lives in the
real-API test group.
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from agent.config import Settings
from agent.slot import ConversationBusy, TurnSlot
from agent.storage import Repo, load_seed

SEED_PATH = Path(__file__).resolve().parents[1] / "seeds" / "seed.json"
HMAC_KEY = "agent-seed-v1"


def _pair_shape(msgs):
    return [(m["msg_seq"], m["role"]) for m in msgs]


def test_t3_two_connections_race(db_path):
    ra, rb = Repo(db_path), Repo(db_path)
    barrier = threading.Barrier(2, timeout=10)

    def attempt(repo, label):
        barrier.wait()
        return repo.try_claim(
            "c-demo", f"{label}的消息", f"inst-{label}", "explain-first", 1,
            client_msg_id=f"m-{label}",
        )

    with ThreadPoolExecutor(2) as ex:
        fa = ex.submit(attempt, ra, "A")
        fb = ex.submit(attempt, rb, "B")
        resa, resb = fa.result(), fb.result()

    assert sorted([resa["kind"], resb["kind"]]) == ["busy", "claimed"]
    a_won = resa["kind"] == "claimed"
    winner, wrepo = (resa, ra) if a_won else (resb, rb)
    loser_label, lrepo = ("B", rb) if a_won else ("A", ra)

    wrepo.finalize_turn(winner["turn"]["id"], winner["turn"]["owner_instance"], 0,
                        status="completed", content="胜者回复")
    r2 = lrepo.try_claim("c-demo", f"{loser_label}的消息", f"inst-{loser_label}",
                         "explain-first", 1, client_msg_id=f"m-{loser_label}")
    assert r2["kind"] == "claimed" and r2["turn"]["turn_seq"] == 2
    lrepo.finalize_turn(r2["turn"]["id"], f"inst-{loser_label}", 0,
                        status="completed", content="败者回复")

    msgs = ra.list_messages("c-demo")
    assert _pair_shape(msgs) == [(1, "user"), (2, "assistant"), (3, "user"), (4, "assistant")]
    assert len({m["turn_id"] for m in msgs}) == 2
    ra.close()
    rb.close()


def _worker(db_path, label, barrier, queue):
    repo = Repo(db_path)
    owner = f"inst-{label}"
    barrier.wait()
    deadline = time.monotonic() + 15
    turn = None
    while time.monotonic() < deadline:
        r = repo.try_claim("c-demo", f"{label}的消息", owner, "explain-first", 1,
                           client_msg_id=f"msg-{label}")
        if r["kind"] == "claimed":
            turn = r["turn"]
            break
        time.sleep(0.02)
    if turn is None:
        queue.put((label, None, None, False))
        repo.close()
        return
    repo.flush_partial(turn["id"], owner, 0, f"{label}的部分")
    ok = repo.finalize_turn(turn["id"], owner, 0, status="completed", content=f"{label}的回复")
    queue.put((label, turn["id"], turn["turn_seq"], ok))
    repo.close()


def test_t3_two_processes(db_path):
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2, timeout=15)
    queue = ctx.Queue()
    procs = [ctx.Process(target=_worker, args=(str(db_path), label, barrier, queue))
             for label in ("P1", "P2")]
    for p in procs:
        p.start()
    results = [queue.get(timeout=30) for _ in range(2)]
    for p in procs:
        p.join(timeout=30)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
    assert all(ok for (_, _, _, ok) in results), results
    assert sorted(seq for (_, _, seq, _) in results) == [1, 2]
    assert len({tid for (_, tid, _, _) in results}) == 2

    repo = Repo(db_path)
    msgs = repo.list_messages("c-demo")
    assert _pair_shape(msgs) == [(1, "user"), (2, "assistant"), (3, "user"), (4, "assistant")]
    by_seq = {m["msg_seq"]: m for m in msgs}
    for label, tid, seq, _ in results:
        assert by_seq[2 * seq - 1]["content"] == f"{label}的消息"
        assert by_seq[2 * seq]["content"] == f"{label}的回复"
        assert by_seq[2 * seq - 1]["turn_id"] == tid == by_seq[2 * seq]["turn_id"]
    assert len(repo.list_turns("c-demo")) == 2
    repo.close()


def test_idempotent_retry(repo):
    r1 = repo.try_claim("c-demo", "你好", "inst-A", "explain-first", 1, client_msg_id="m1")
    tid = r1["turn"]["id"]
    r2 = repo.try_claim("c-demo", "你好", "inst-A", "explain-first", 1, client_msg_id="m1")
    assert r2["kind"] == "exists" and r2["turn"]["id"] == tid
    assert len(repo.list_turns("c-demo")) == 1
    assert len(repo.list_messages("c-demo")) == 2
    repo.finalize_turn(tid, "inst-A", 0, status="completed", content="回复")
    r3 = repo.try_claim("c-demo", "你好", "inst-A", "explain-first", 1, client_msg_id="m1")
    assert r3["kind"] == "exists" and r3["turn"]["status"] == "completed"


def test_fencing_rejects_stale_writes(repo):
    repo.heartbeat("inst-live")
    cid2 = repo.create_conversation("t-quadratic", "u-demo", "explain-first", 1)["id"]
    live = repo.try_claim("c-demo", "活轮", "inst-live", "explain-first", 1, client_msg_id="live")["turn"]
    stale = repo.try_claim(cid2, "死轮", "inst-dead", "explain-first", 1, client_msg_id="s1")["turn"]
    sid, owner = stale["id"], "inst-dead"
    assert repo.flush_partial(sid, owner, 0, "半截回复")

    reclaimed = repo.reclaim_stale(5.0)
    assert sid in reclaimed and live["id"] not in reclaimed
    row = repo.get_turn(sid)
    assert row["status"] == "interrupted" and row["fence_token"] == 1

    assert not repo.write_plan(sid, owner, 0, actual_strategy="explain-first", actual_version=1,
                               fallback_reason=None, plan_doc={}, plan_schema_version=1,
                               context_builder_version="cb-v1", preselected_snippets=[])
    assert not repo.flush_partial(sid, owner, 0, "stale覆盖")
    assert not repo.append_model_call(sid, owner, 0, call_seq=1, model_id="m", params={},
                                      input_tokens=1, output_tokens=1, latency_ms=1)
    assert not repo.append_tool_call(sid, owner, 0, call_seq=2, tool_name="t", args={},
                                     result_ref=None, latency_ms=1, was_allowed=True)
    assert not repo.add_context_snippets(sid, owner, 0, ["snippet-x"])
    assert not repo.append_answer_event(sid, owner, 0, user_id="u-demo", topic_id="t-quadratic",
                                        conv_id=cid2, question_id="q", user_answer="a",
                                        expected="b", correct=False)
    assert not repo.finalize_turn(sid, owner, 0, status="completed", content="复活")

    assert repo.get_turn(sid)["status"] == "interrupted"
    assert repo.get_model_calls(sid) == []
    assert repo.get_tool_calls(sid) == []
    assert repo.get_turn_context(sid) == []
    partial = repo.turn_messages(sid)[1]
    assert partial["content"] == "半截回复" and partial["is_partial"] == 1
    assert repo.get_turn(live["id"])["status"] == "running"


def test_slot_wait_timeout_and_acquire(db_path):
    settings = replace(Settings(), slot_wait_timeout=0.3, slot_poll_interval=0.05)
    ra, rb = Repo(db_path), Repo(db_path)

    async def run():
        sa, sb = TurnSlot(ra, settings), TurnSlot(rb, settings)
        r1 = await sa.claim("c-demo", "A", "inst-A", "explain-first", 1, client_msg_id="a1")
        t0 = time.monotonic()
        with pytest.raises(ConversationBusy):
            await sb.claim("c-demo", "B", "inst-B", "explain-first", 1, client_msg_id="b1")
        assert time.monotonic() - t0 >= 0.25
        dup = await sb.claim("c-demo", "A", "inst-B", "explain-first", 1, client_msg_id="a1")
        assert dup["kind"] == "exists" and dup["turn"]["id"] == r1["turn"]["id"]
        ra.finalize_turn(r1["turn"]["id"], "inst-A", 0, status="completed", content="A回复")
        r2 = await sb.claim("c-demo", "B", "inst-B", "explain-first", 1, client_msg_id="b1")
        assert r2["kind"] == "claimed" and r2["turn"]["turn_seq"] == 2

    asyncio.run(run())
    ra.close()
    rb.close()


def test_finalize_preserves_fallback_reason(repo):
    t = repo.try_claim("c-demo", "问", "inst-A", "faulty", 1)["turn"]
    assert repo.write_plan(t["id"], "inst-A", 0, actual_strategy="explain-first", actual_version=1,
                           fallback_reason="strategy_plan_error", plan_doc={}, plan_schema_version=1,
                           context_builder_version="cb-v1", preselected_snippets=[])
    assert repo.finalize_turn(t["id"], "inst-A", 0, status="completed", content="回复")
    assert repo.get_turn(t["id"])["fallback_reason"] == "strategy_plan_error"


def test_recovery_owner_aware(repo):
    repo.heartbeat("inst-A")
    cid2 = repo.create_conversation("t-quadratic", "u-demo", "explain-first", 1)["id"]
    ta = repo.try_claim("c-demo", "A轮", "inst-A", "explain-first", 1)["turn"]["id"]
    tb = repo.try_claim(cid2, "B轮", "inst-B", "explain-first", 1)["turn"]["id"]
    reclaimed = repo.reclaim_stale(5.0)
    assert reclaimed == [tb]
    assert repo.get_turn(ta)["status"] == "running"
    assert repo.get_turn(tb)["status"] == "interrupted"


def test_seed_idempotent_and_manifest(db_path):
    repo = Repo(db_path)
    h1 = repo.get_meta("seed_manifest_hash")
    h2 = load_seed(repo, SEED_PATH, HMAC_KEY)
    assert h1 == h2
    assert len(repo.get_snippets("t-quadratic")) == 7
    assert len(repo.get_questions("t-quadratic")) == 6
    repo.close()
