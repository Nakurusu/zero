"""HTTP API + SSE (A1/A2/A4, D2.6/D8.5) and composition root."""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .channel import DeltaChannel
from .config import Settings
from .executor import ExecutorDeps, run_turn
from .model_client import OpenAICompatClient
from .recovery import Recovery, TaskRegistry
from .slot import ConversationBusy, TurnSlot
from .storage.repo import Repo
from .strategies import StrategyRegistry, default_registry
from .tools import ToolRegistry, default_tool_registry

TERMINAL = ("done", "error", "interrupted")


class MessageIn(BaseModel):
    content: str = Field(min_length=1)
    client_msg_id: str | None = None


class ConversationIn(BaseModel):
    topic_id: str
    user_id: str
    strategy_name: str | None = None
    strategy_version: int | None = None


class StrategyIn(BaseModel):
    strategy_name: str
    strategy_version: int


def _sse(type_: str, data: dict) -> str:
    return f"event: {type_}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _latency_ms(turn: dict) -> int | None:
    if not turn.get("finished_at"):
        return None
    start = datetime.fromisoformat(turn["started_at"])
    end = datetime.fromisoformat(turn["finished_at"])
    return int((end - start).total_seconds() * 1000)


def _turn_block(turn: dict) -> dict:
    return {
        "turn_id": turn["id"],
        "turn_seq": turn["turn_seq"],
        "status": turn["status"],
        "planned_strategy": turn["planned_strategy"],
        "planned_version": turn["planned_version"],
        "actual_strategy": turn["actual_strategy"],
        "actual_version": turn["actual_version"],
        "fallback_reason": turn["fallback_reason"],
        "error_code": turn["error_code"],
        "interrupt_reason": turn["interrupt_reason"],
        "input_tokens": turn.get("input_tokens"),
        "output_tokens": turn.get("output_tokens"),
        "tool_count": turn.get("tool_count"),
        "latency_ms": _latency_ms(turn),
        "eval_run_id": turn["eval_run_id"],
        "case_id": turn["case_id"],
        "turn_index": turn["turn_index"],
        "started_at": turn["started_at"],
        "finished_at": turn["finished_at"],
    }


def _terminal_event(turn: dict) -> tuple[str, dict]:
    status = turn["status"]
    if status == "completed":
        return "done", {"turn_id": turn["id"], "status": "completed"}
    if status == "failed":
        return "error", {"turn_id": turn["id"], "error_code": turn["error_code"]}
    return "interrupted", {"turn_id": turn["id"], "reason": turn["interrupt_reason"]}


def create_app(
    settings: Settings | None = None,
    *,
    strategies: StrategyRegistry | None = None,
    tools: ToolRegistry | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    strategy_registry = strategies or default_registry()
    tool_registry = tools or default_tool_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repo = Repo(settings.db_path)
        instance = uuid.uuid4().hex
        repo.heartbeat(instance)
        recovery = Recovery(repo, settings, instance)
        recovery.reclaim_now()
        channel = DeltaChannel()
        runner = TaskRegistry()
        deps = ExecutorDeps(repo, settings, strategy_registry, tool_registry,
                            OpenAICompatClient(settings), channel, instance)
        app.state.deps = deps
        app.state.slot = TurnSlot(repo, settings)
        app.state.runner = runner
        app.state.instance = instance
        hb = asyncio.create_task(recovery.heartbeat_loop())
        reaper = asyncio.create_task(recovery.reaper_loop())
        try:
            yield
        finally:
            _, pending = await runner.drain(settings.drain_deadline)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            hb.cancel()
            reaper.cancel()
            await deps.model.close()
            repo.close()

    app = FastAPI(title="Teaching Agent Conversation Service", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(request: Request):
        return {"status": "ok", "instance_uuid": request.app.state.instance}

    @app.post("/conversations", status_code=201)
    async def create_conversation(body: ConversationIn, request: Request):
        repo = request.app.state.deps.repo
        if not repo.con.execute("SELECT 1 FROM topics WHERE id=?", (body.topic_id,)).fetchone():
            raise HTTPException(404, "topic_not_found")
        if not repo.con.execute("SELECT 1 FROM users WHERE id=?", (body.user_id,)).fetchone():
            raise HTTPException(404, "user_not_found")
        name = body.strategy_name or settings.default_strategy
        version = body.strategy_version if body.strategy_version is not None else settings.default_strategy_version
        if not strategy_registry.has(name, version):
            raise HTTPException(400, f"unknown_strategy:{name}@{version}")
        return repo.create_conversation(body.topic_id, body.user_id, name, version)

    @app.patch("/conversations/{conv_id}/strategy")
    async def set_strategy(conv_id: str, body: StrategyIn, request: Request):
        repo = request.app.state.deps.repo
        if not repo.get_conversation(conv_id):
            raise HTTPException(404, "conversation_not_found")
        if not strategy_registry.has(body.strategy_name, body.strategy_version):
            raise HTTPException(400, f"unknown_strategy:{body.strategy_name}@{body.strategy_version}")
        repo.set_conversation_strategy(conv_id, body.strategy_name, body.strategy_version)
        return repo.get_conversation(conv_id)

    @app.get("/conversations/{conv_id}/messages")
    async def get_messages(conv_id: str, request: Request):
        repo = request.app.state.deps.repo
        if not repo.get_conversation(conv_id):
            raise HTTPException(404, "conversation_not_found")
        messages = [
            {"id": m["id"], "turn_id": m["turn_id"], "role": m["role"], "content": m["content"],
             "msg_seq": m["msg_seq"], "is_partial": bool(m["is_partial"]), "created_at": m["created_at"]}
            for m in repo.list_messages(conv_id)
        ]
        turns = [_turn_block(t) for t in repo.list_turns(conv_id)]
        return {"messages": messages, "turns": turns}

    @app.get("/conversations/{conv_id}/turns/{turn_id}")
    async def get_turn_audit(conv_id: str, turn_id: str, request: Request):
        repo = request.app.state.deps.repo
        turn = repo.get_turn(turn_id)
        if not turn or turn["conversation_id"] != conv_id:
            raise HTTPException(404, "turn_not_found")
        turns = repo.list_turns(conv_id)
        metrics = next(t for t in turns if t["id"] == turn_id)
        return {
            "turn": _turn_block(metrics),
            "messages": repo.turn_messages(turn_id),
            "plan_doc": json.loads(turn["plan_doc"]) if turn["plan_doc"] else None,
            "model_calls": [
                {**c, "params": json.loads(c["params"])} for c in repo.get_model_calls(turn_id)
            ],
            "tool_calls": [
                {**c, "args": json.loads(c["args"]),
                 "result_ref": json.loads(c["result_ref"]) if c["result_ref"] else None,
                 "was_allowed": bool(c["was_allowed"])}
                for c in repo.get_tool_calls(turn_id)
            ],
            "turn_context": repo.get_turn_context(turn_id),
        }

    @app.post("/conversations/{conv_id}/turns/{turn_id}/cancel")
    async def cancel_turn(conv_id: str, turn_id: str, request: Request):
        repo = request.app.state.deps.repo
        turn = repo.get_turn(turn_id)
        if not turn or turn["conversation_id"] != conv_id:
            raise HTTPException(404, "turn_not_found")
        updated = repo.request_cancel(turn_id)
        return {"turn_id": turn_id, "status": updated["status"],
                "cancel_requested": bool(updated["cancel_requested"])}

    @app.post("/conversations/{conv_id}/messages")
    async def post_message(conv_id: str, body: MessageIn, request: Request):
        state = request.app.state
        repo = state.deps.repo
        conv = repo.get_conversation(conv_id)
        if not conv:
            raise HTTPException(404, "conversation_not_found")
        try:
            result = await state.slot.claim(
                conv_id, body.content, state.instance,
                conv["strategy_name"], conv["strategy_version"],
                client_msg_id=body.client_msg_id,
            )
        except ConversationBusy as exc:
            return JSONResponse(
                {"error_code": "conversation_busy", "conversation_id": conv_id,
                 "retry_after": exc.retry_after},
                status_code=409, headers={"Retry-After": str(exc.retry_after)},
            )
        turn = result["turn"]
        if result["kind"] == "claimed":
            state.deps.channel.publish(turn["id"], "turn_started", {
                "turn_id": turn["id"], "turn_seq": turn["turn_seq"],
                "planned_strategy": turn["planned_strategy"], "planned_version": turn["planned_version"],
            })
            state.runner.spawn(run_turn(state.deps, turn))
            stream = _live_stream(state, turn["id"])
        else:
            stream = _existing_stream(state, turn)
        return StreamingResponse(stream, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def _live_stream(state, turn_id: str):
        async for event in state.deps.channel.subscribe(turn_id):
            yield _sse(event.type, event.data)

    async def _existing_stream(state, turn: dict):
        """Idempotent hit: replay terminal, subscribe local running, or DB-poll remote running (2A)."""
        repo = state.deps.repo
        yield _sse("turn_started", {"turn_id": turn["id"], "turn_seq": turn["turn_seq"],
                                    "planned_strategy": turn["planned_strategy"],
                                    "planned_version": turn["planned_version"], "replayed": True})
        if turn["status"] != "running":
            assistant = next((m for m in repo.turn_messages(turn["id"]) if m["role"] == "assistant"), None)
            if assistant and assistant["content"]:
                yield _sse("delta", {"content": assistant["content"]})
            type_, data = _terminal_event(turn)
            yield _sse(type_, data)
            return
        if turn["owner_instance"] == state.instance:
            async for event in state.deps.channel.subscribe(turn["id"]):
                yield _sse(event.type, event.data)
            return
        sent = 0
        while True:
            assistant = next((m for m in repo.turn_messages(turn["id"]) if m["role"] == "assistant"), None)
            content = assistant["content"] if assistant else ""
            if len(content) > sent:
                yield _sse("delta", {"content": content[sent:]})
                sent = len(content)
            current = repo.get_turn(turn["id"])
            if current["status"] != "running":
                type_, data = _terminal_event(current)
                yield _sse(type_, data)
                return
            await asyncio.sleep(0.3)

    return app
