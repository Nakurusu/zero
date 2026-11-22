from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = Path(__file__).with_name("schema.sql")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


class Repo:
    """SQLite(WAL) implementation of the storage seam.

    All executor writes are fenced: they only land while the turn is still
    running under the same owner_instance and fence_token captured at claim.
    Recovery bumps fence_token, so a stale owner's writes match zero rows.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.db_path, timeout=10, isolation_level=None, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=10000")
        self.con.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def close(self) -> None:
        self.con.close()

    def init_schema(self) -> None:
        self.con.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self.con.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', '1')"
        )

    # ---------- metadata / seed ----------

    def get_meta(self, key: str) -> str | None:
        row = self.con.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.con.execute(
            "INSERT INTO metadata(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ---------- conversations / preset data ----------

    def create_conversation(
        self, topic_id: str, user_id: str, strategy_name: str, strategy_version: int,
        conv_id: str | None = None,
    ) -> dict:
        conv_id = conv_id or new_id()
        self.con.execute(
            "INSERT INTO conversations(id, topic_id, user_id, strategy_name, strategy_version, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (conv_id, topic_id, user_id, strategy_name, strategy_version, now_iso()),
        )
        return self.get_conversation(conv_id)

    def get_conversation(self, conv_id: str) -> dict | None:
        return _row(self.con.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone())

    def set_conversation_strategy(self, conv_id: str, name: str, version: int) -> bool:
        cur = self.con.execute(
            "UPDATE conversations SET strategy_name=?, strategy_version=? WHERE id=?",
            (name, version, conv_id),
        )
        return cur.rowcount > 0

    def get_snippets(self, topic_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM material_snippets WHERE topic_id=? ORDER BY id", (topic_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_snippet(self, snippet_id: str) -> dict | None:
        return _row(self.con.execute("SELECT * FROM material_snippets WHERE id=?", (snippet_id,)).fetchone())

    def get_questions(self, topic_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM questions WHERE topic_id=? ORDER BY id", (topic_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_question(self, question_id: str) -> dict | None:
        return _row(self.con.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone())

    def get_learning_state(self, user_id: str, topic_id: str) -> dict | None:
        return _row(self.con.execute(
            "SELECT * FROM learning_state WHERE user_id=? AND topic_id=?", (user_id, topic_id)
        ).fetchone())

    # ---------- claim / turn lifecycle ----------

    def try_claim(
        self,
        conv_id: str,
        content: str,
        owner_instance: str,
        planned_strategy: str,
        planned_version: int,
        client_msg_id: str | None = None,
        parent_turn_id: str | None = None,
        eval_run_id: str | None = None,
        case_id: str | None = None,
        turn_index: int | None = None,
    ) -> dict:
        """One atomic claim attempt. Returns kind: claimed | exists | busy."""
        self.con.execute("BEGIN IMMEDIATE")
        try:
            if client_msg_id is not None:
                row = self.con.execute(
                    "SELECT * FROM turns WHERE conversation_id=? AND client_msg_id=?",
                    (conv_id, client_msg_id),
                ).fetchone()
                if row:
                    self.con.execute("COMMIT")
                    return {"kind": "exists", "turn": dict(row)}
            if self.con.execute(
                "SELECT 1 FROM turns WHERE conversation_id=? AND status='running'", (conv_id,)
            ).fetchone():
                self.con.execute("COMMIT")
                return {"kind": "busy"}
            seq = self.con.execute(
                "SELECT COALESCE(MAX(turn_seq), 0) + 1 FROM turns WHERE conversation_id=?",
                (conv_id,),
            ).fetchone()[0]
            turn_id = new_id()
            ts = now_iso()
            self.con.execute(
                "INSERT INTO turns(id, conversation_id, turn_seq, status, planned_strategy, planned_version, "
                "owner_instance, fence_token, client_msg_id, parent_turn_id, eval_run_id, case_id, turn_index, started_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (turn_id, conv_id, seq, "running", planned_strategy, planned_version,
                 owner_instance, 0, client_msg_id, parent_turn_id, eval_run_id, case_id, turn_index, ts),
            )
            self.con.execute(
                "INSERT INTO messages(id, conversation_id, turn_id, role, content, msg_seq, is_partial, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (new_id(), conv_id, turn_id, "user", content, 2 * seq - 1, 0, ts),
            )
            self.con.execute(
                "INSERT INTO messages(id, conversation_id, turn_id, role, content, msg_seq, is_partial, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (new_id(), conv_id, turn_id, "assistant", "", 2 * seq, 1, ts),
            )
            self.con.execute("COMMIT")
            return {"kind": "claimed", "turn": self.get_turn(turn_id)}
        except sqlite3.IntegrityError as exc:
            self.con.execute("ROLLBACK")
            msg = str(exc)
            if "client_msg_id" in msg:
                row = self.con.execute(
                    "SELECT * FROM turns WHERE conversation_id=? AND client_msg_id=?",
                    (conv_id, client_msg_id),
                ).fetchone()
                return {"kind": "exists", "turn": dict(row)}
            # Partial unique index reports the column name, not the index name.
            if "turns.conversation_id" in msg:
                return {"kind": "busy"}
            raise

    def get_turn(self, turn_id: str) -> dict | None:
        return _row(self.con.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone())

    def list_turns(self, conv_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT t.*, "
            "(SELECT COALESCE(SUM(input_tokens),0) FROM model_calls WHERE turn_id=t.id) AS input_tokens, "
            "(SELECT COALESCE(SUM(output_tokens),0) FROM model_calls WHERE turn_id=t.id) AS output_tokens, "
            "(SELECT COUNT(*) FROM tool_calls WHERE turn_id=t.id) AS tool_count "
            "FROM turns t WHERE t.conversation_id=? ORDER BY t.turn_seq",
            (conv_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_messages(self, conv_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY msg_seq", (conv_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def turn_messages(self, turn_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM messages WHERE turn_id=? ORDER BY msg_seq", (turn_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def history_turns(self, conv_id: str) -> list[dict]:
        """Completed turns with their message pairs, oldest first (context candidates)."""
        turns = self.con.execute(
            "SELECT * FROM turns WHERE conversation_id=? AND status='completed' ORDER BY turn_seq",
            (conv_id,),
        ).fetchall()
        out = []
        for t in turns:
            msgs = self.turn_messages(t["id"])
            out.append({"turn": dict(t), "messages": msgs})
        return out

    # ---------- fenced writes (executor) ----------

    def _owns(self, turn_id: str, owner: str, fence: int) -> bool:
        row = self.con.execute(
            "SELECT 1 FROM turns WHERE id=? AND status='running' AND owner_instance=? AND fence_token=?",
            (turn_id, owner, fence),
        ).fetchone()
        return row is not None

    def write_plan(
        self, turn_id: str, owner: str, fence: int, *,
        actual_strategy: str, actual_version: int, fallback_reason: str | None,
        plan_doc: dict, plan_schema_version: int, context_builder_version: str,
        preselected_snippets: list[str],
    ) -> bool:
        self.con.execute("BEGIN IMMEDIATE")
        cur = self.con.execute(
            "UPDATE turns SET actual_strategy=?, actual_version=?, fallback_reason=?, "
            "plan_schema_version=?, context_builder_version=?, plan_doc=? "
            "WHERE id=? AND status='running' AND owner_instance=? AND fence_token=?",
            (actual_strategy, actual_version, fallback_reason, plan_schema_version,
             context_builder_version, json.dumps(plan_doc, ensure_ascii=False),
             turn_id, owner, fence),
        )
        if cur.rowcount == 0:
            self.con.execute("ROLLBACK")
            return False
        for sid in preselected_snippets:
            self.con.execute(
                "INSERT OR IGNORE INTO turn_context(turn_id, snippet_id, source) VALUES(?,?, 'preselected')",
                (turn_id, sid),
            )
        self.con.execute("COMMIT")
        return True

    def flush_partial(self, turn_id: str, owner: str, fence: int, content: str) -> bool:
        cur = self.con.execute(
            "UPDATE messages SET content=?, is_partial=1 "
            "WHERE turn_id=? AND role='assistant' AND EXISTS ("
            "SELECT 1 FROM turns WHERE id=messages.turn_id AND status='running' "
            "AND owner_instance=? AND fence_token=?)",
            (content, turn_id, owner, fence),
        )
        return cur.rowcount > 0

    def append_model_call(
        self, turn_id: str, owner: str, fence: int, *, call_seq: int, model_id: str,
        params: dict, input_tokens: int | None, output_tokens: int | None, latency_ms: int,
    ) -> bool:
        cur = self.con.execute(
            "INSERT INTO model_calls(turn_id, call_seq, model_id, params, input_tokens, output_tokens, latency_ms, created_at) "
            "SELECT ?,?,?,?,?,?,?,? WHERE EXISTS ("
            "SELECT 1 FROM turns WHERE id=? AND status='running' AND owner_instance=? AND fence_token=?)",
            (turn_id, call_seq, model_id, json.dumps(params, ensure_ascii=False),
             input_tokens, output_tokens, latency_ms, now_iso(), turn_id, owner, fence),
        )
        return cur.rowcount > 0

    def append_tool_call(
        self, turn_id: str, owner: str, fence: int, *, call_seq: int, tool_name: str,
        args: dict, result_ref: Any, latency_ms: int, was_allowed: bool,
    ) -> bool:
        cur = self.con.execute(
            "INSERT INTO tool_calls(turn_id, call_seq, tool_name, args, result_ref, latency_ms, was_allowed, created_at) "
            "SELECT ?,?,?,?,?,?,?,? WHERE EXISTS ("
            "SELECT 1 FROM turns WHERE id=? AND status='running' AND owner_instance=? AND fence_token=?)",
            (turn_id, call_seq, tool_name, json.dumps(args, ensure_ascii=False),
             json.dumps(result_ref, ensure_ascii=False), latency_ms, int(was_allowed), now_iso(),
             turn_id, owner, fence),
        )
        return cur.rowcount > 0

    def add_context_snippets(self, turn_id: str, owner: str, fence: int, snippet_ids: list[str]) -> bool:
        if not self._owns(turn_id, owner, fence):
            return False
        for sid in snippet_ids:
            self.con.execute(
                "INSERT OR IGNORE INTO turn_context(turn_id, snippet_id, source) VALUES(?,?, 'tool')",
                (turn_id, sid),
            )
        return True

    def append_answer_event(
        self, turn_id: str, owner: str, fence: int, *, user_id: str, topic_id: str,
        conv_id: str, question_id: str, user_answer: str, expected: str, correct: bool,
    ) -> bool:
        cur = self.con.execute(
            "INSERT INTO answer_events(user_id, topic_id, conversation_id, turn_id, question_id, "
            "user_answer, expected, correct, created_at) "
            "SELECT ?,?,?,?,?,?,?,?,? WHERE EXISTS ("
            "SELECT 1 FROM turns WHERE id=? AND status='running' AND owner_instance=? AND fence_token=?)",
            (user_id, topic_id, conv_id, turn_id, question_id, user_answer, expected,
             int(correct), now_iso(), turn_id, owner, fence),
        )
        return cur.rowcount > 0

    def finalize_turn(
        self, turn_id: str, owner: str, fence: int, *, status: str,
        content: str | None = None, error_code: str | None = None, interrupt_reason: str | None = None,
        fallback_reason: str | None = None,
    ) -> bool:
        assert status in ("completed", "failed", "interrupted")
        self.con.execute("BEGIN IMMEDIATE")
        cur = self.con.execute(
            "UPDATE turns SET status=?, finished_at=?, error_code=?, interrupt_reason=?, "
            "fallback_reason=COALESCE(?, fallback_reason) "
            "WHERE id=? AND status='running' AND owner_instance=? AND fence_token=?",
            (status, now_iso(), error_code, interrupt_reason, fallback_reason, turn_id, owner, fence),
        )
        if cur.rowcount == 0:
            self.con.execute("ROLLBACK")
            return False
        if status == "completed":
            self.con.execute(
                "UPDATE messages SET content=?, is_partial=0 WHERE turn_id=? AND role='assistant'",
                (content or "", turn_id),
            )
        self.con.execute("COMMIT")
        return True

    def request_cancel(self, turn_id: str) -> dict | None:
        self.con.execute(
            "UPDATE turns SET cancel_requested=1 WHERE id=? AND status='running'", (turn_id,)
        )
        return self.get_turn(turn_id)

    def cancel_requested(self, turn_id: str) -> bool:
        row = self.con.execute(
            "SELECT cancel_requested FROM turns WHERE id=?", (turn_id,)
        ).fetchone()
        return bool(row and row["cancel_requested"])

    def turn_control(self, turn_id: str) -> dict | None:
        """One cheap read for the executor's between-chunk ownership/cancel poll."""
        return _row(self.con.execute(
            "SELECT status, cancel_requested, owner_instance, fence_token FROM turns WHERE id=?",
            (turn_id,),
        ).fetchone())

    # ---------- liveness / recovery ----------

    def heartbeat(self, instance_uuid: str) -> None:
        self.con.execute(
            "INSERT INTO worker_liveness(instance_uuid, last_heartbeat) VALUES(?, ?) "
            "ON CONFLICT(instance_uuid) DO UPDATE SET last_heartbeat=excluded.last_heartbeat",
            (instance_uuid, now_iso()),
        )

    def reclaim_stale(self, ttl_seconds: float, reason: str = "process_restart") -> list[str]:
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - ttl_seconds, tz=timezone.utc
        ).isoformat(timespec="milliseconds")
        self.con.execute("BEGIN IMMEDIATE")
        rows = self.con.execute(
            "SELECT id FROM turns WHERE status='running' AND (owner_instance IS NULL OR owner_instance NOT IN "
            "(SELECT instance_uuid FROM worker_liveness WHERE last_heartbeat >= ?))",
            (cutoff,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            self.con.execute(
                "UPDATE turns SET status='interrupted', interrupt_reason=?, finished_at=?, "
                "fence_token=fence_token+1 "
                "WHERE status='running' AND (owner_instance IS NULL OR owner_instance NOT IN "
                "(SELECT instance_uuid FROM worker_liveness WHERE last_heartbeat >= ?))",
                (reason, now_iso(), cutoff),
            )
        self.con.execute("COMMIT")
        return ids

    def list_running(self) -> list[dict]:
        rows = self.con.execute("SELECT * FROM turns WHERE status='running'").fetchall()
        return [dict(r) for r in rows]

    # ---------- audit reads / eval ----------

    def get_model_calls(self, turn_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM model_calls WHERE turn_id=? ORDER BY call_seq", (turn_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_tool_calls(self, turn_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM tool_calls WHERE turn_id=? ORDER BY call_seq", (turn_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_turn_context(self, turn_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM turn_context WHERE turn_id=? ORDER BY snippet_id", (turn_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def create_eval_run(
        self, run_id: str, seed_manifest_hash: str, model_id: str, params: dict,
        strategy_versions: dict, checks_version: str,
    ) -> None:
        self.con.execute(
            "INSERT INTO eval_runs(run_id, seed_manifest_hash, model_id, params, strategy_versions, checks_version, started_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, seed_manifest_hash, model_id, json.dumps(params, ensure_ascii=False),
             json.dumps(strategy_versions, ensure_ascii=False), checks_version, now_iso()),
        )

    def finish_eval_run(self, run_id: str) -> None:
        self.con.execute(
            "UPDATE eval_runs SET finished_at=? WHERE run_id=?", (now_iso(), run_id)
        )

    def get_eval_run(self, run_id: str) -> dict | None:
        return _row(self.con.execute("SELECT * FROM eval_runs WHERE run_id=?", (run_id,)).fetchone())

    def turns_for_run(self, run_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM turns WHERE eval_run_id=? ORDER BY case_id, turn_index", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def insert_check_results(self, rows: list[dict]) -> None:
        self.con.executemany(
            "INSERT INTO eval_check_results(run_id, case_id, turn_index, strategy, check_name, passed, detail) "
            "VALUES(:run_id, :case_id, :turn_index, :strategy, :check_name, :passed, :detail)",
            rows,
        )

    def check_results_for_run(self, run_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM eval_check_results WHERE run_id=? ORDER BY case_id, turn_index, check_name",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
