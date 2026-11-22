"""Common turn executor (B1): model<->tool loop, streaming, persistence, audit.

Position-independent execution unit (seam discipline 1): depends only on
(turn dict, injected deps), never on request/FastAPI context. All writes are
fenced; losing ownership aborts the turn without writing anything further.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from .channel import DeltaChannel
from .config import Settings
from .context import HistoryTurn, PlanInput, StrategyConfig, TurnPlan
from .model_client import Delta, Finish, ModelError, ToolCall, Usage
from .storage.repo import Repo
from .strategies import DefaultStrategyFailed, StrategyRegistry, make_validator, plan_with_fallback
from .tools import ToolContext, ToolRegistry, ToolResult

logger = logging.getLogger("agent.executor")


@dataclass
class ExecutorDeps:
    repo: Repo
    settings: Settings
    strategies: StrategyRegistry
    tools: ToolRegistry
    model: object  # ModelClient seam: stream_chat(messages, tools) -> AsyncIterator[Event]
    channel: DeltaChannel
    instance_uuid: str


def render_prompt(plan: TurnPlan, plan_input: PlanInput) -> list[dict]:
    """Render the model context from pure references (D7.1); dangling refs skip gracefully."""
    system = plan.instructions
    if plan_input.learning_digest:
        system += "\n\n【学习状态】" + json.dumps(plan_input.learning_digest, ensure_ascii=False)
    if plan_input.question_bank:
        system += "\n\n【题库】\n" + "\n".join(
            f"- {q['id']}: {q['prompt']}（知识点：{q['knowledge_point']}）"
            for q in plan_input.question_bank
        )
    materials = {m["id"]: m for m in plan_input.material_candidates}
    preselected = [materials[sid] for sid in plan.context.snippet_ids if sid in materials]
    if preselected:
        system += "\n\n【预选材料】\n" + "\n".join(
            f"《{s['title']}》：{s['content']}" for s in preselected
        )

    by_id: dict[str, tuple[str, str]] = {}
    for turn in plan_input.history:
        by_id[turn.user_message_id] = ("user", turn.user_content)
        by_id[turn.assistant_message_id] = ("assistant", turn.assistant_content)
    messages = [{"role": "system", "content": system}]
    for mid in plan.context.message_ids:
        if mid in by_id:
            role, content = by_id[mid]
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": plan_input.user_message})
    return messages


async def run_turn(deps: ExecutorDeps, turn: dict) -> None:
    repo = deps.repo
    settings = deps.settings
    turn_id = turn["id"]
    owner = turn["owner_instance"]
    fence = turn["fence_token"]
    log = logger

    def publish(type_: str, data: dict) -> None:
        deps.channel.publish(turn_id, type_, data)

    def fence_lost() -> None:
        log.warning("turn_id=%s ownership lost (reclaimed), aborting without writes", turn_id)
        publish("interrupted", {"turn_id": turn_id, "reason": "reclaimed"})

    def fail(error_code: str, fallback_reason: str | None = None) -> None:
        repo.finalize_turn(turn_id, owner, fence, status="failed",
                           error_code=error_code, fallback_reason=fallback_reason)
        log.info("turn_id=%s failed error_code=%s", turn_id, error_code)
        publish("error", {"turn_id": turn_id, "error_code": error_code})

    def interrupt(reason: str) -> None:
        repo.finalize_turn(turn_id, owner, fence, status="interrupted", interrupt_reason=reason)
        log.info("turn_id=%s interrupted reason=%s", turn_id, reason)
        publish("interrupted", {"turn_id": turn_id, "reason": reason})

    def still_owner() -> bool:
        ctl = repo.turn_control(turn_id)
        return (ctl is not None and ctl["status"] == "running"
                and ctl["owner_instance"] == owner and ctl["fence_token"] == fence)

    accumulated = ""
    call_seq = 0
    try:
        conv = repo.get_conversation(turn["conversation_id"])
        history = [
            HistoryTurn.from_pair(h["turn"]["id"], h["messages"][0], h["messages"][1])
            for h in repo.history_turns(conv["id"])
        ]
        user_message = next(m["content"] for m in repo.turn_messages(turn_id) if m["role"] == "user")
        ls = repo.get_learning_state(conv["user_id"], conv["topic_id"])
        plan_input = PlanInput(
            user_message=user_message,
            history=history,
            learning_digest=json.loads(ls["digest"]) if ls else None,
            material_candidates=repo.get_snippets(conv["topic_id"]),
            question_bank=repo.get_questions(conv["topic_id"]),
            conversation_meta={"conversation_id": conv["id"], "topic_id": conv["topic_id"],
                               "user_id": conv["user_id"]},
            config=StrategyConfig(settings.history_token_budget, settings.history_max_turns,
                                  settings.preselect_cap),
        )
        validator = make_validator(
            deps.tools.names(),
            {mid for t in history for mid in (t.user_message_id, t.assistant_message_id)},
            {m["id"] for m in plan_input.material_candidates},
        )
        try:
            outcome = plan_with_fallback(
                deps.strategies, turn["planned_strategy"], turn["planned_version"],
                settings.default_strategy, settings.default_strategy_version,
                plan_input, validator,
            )
        except DefaultStrategyFailed as exc:
            fail("strategy_plan_failed", fallback_reason=exc.original_reason)
            return
        plan = outcome.plan
        if outcome.fallback_reason:
            log.info("turn_id=%s fallback planned=%s@%s actual=%s@%s reason=%s",
                     turn_id, turn["planned_strategy"], turn["planned_version"],
                     outcome.actual_name, outcome.actual_version, outcome.fallback_reason)
        if not repo.write_plan(
            turn_id, owner, fence,
            actual_strategy=outcome.actual_name, actual_version=outcome.actual_version,
            fallback_reason=outcome.fallback_reason, plan_doc=plan.to_doc(),
            plan_schema_version=plan.plan_schema_version,
            context_builder_version=plan.context_builder_version,
            preselected_snippets=plan.context.snippet_ids,
        ):
            fence_lost()
            return

        messages = render_prompt(plan, plan_input)
        tool_schemas = deps.tools.schemas(plan.allowed_tools)
        tool_ctx = ToolContext(repo=repo, conversation=conv, turn_id=turn_id, owner=owner, fence=fence)
        iteration = 0
        while True:
            iteration += 1
            if iteration > settings.max_tool_iterations:
                fail("tool_loop_limit")
                return
            call_seq += 1
            call_text = ""
            pending: list[ToolCall] = []
            usage: Usage | None = None
            started = time.monotonic()
            last_flush = started
            stream = deps.model.stream_chat(messages, tool_schemas)
            try:
                async for event in stream:
                    ctl = repo.turn_control(turn_id)
                    if (ctl is None or ctl["status"] != "running"
                            or ctl["owner_instance"] != owner or ctl["fence_token"] != fence):
                        fence_lost()
                        return
                    if ctl["cancel_requested"]:
                        repo.flush_partial(turn_id, owner, fence, accumulated)
                        interrupt("user_cancel")
                        return
                    if isinstance(event, Delta):
                        call_text += event.content
                        accumulated += event.content
                        publish("delta", {"content": event.content})
                        if time.monotonic() - last_flush >= settings.partial_flush_interval:
                            if not repo.flush_partial(turn_id, owner, fence, accumulated):
                                fence_lost()
                                return
                            last_flush = time.monotonic()
                    elif isinstance(event, ToolCall):
                        pending.append(event)
                    elif isinstance(event, Usage):
                        usage = event
            finally:
                await stream.aclose()
            latency_ms = int((time.monotonic() - started) * 1000)
            if not repo.append_model_call(
                turn_id, owner, fence, call_seq=call_seq,
                model_id=settings.model_params.model, params=settings.model_params.as_dict(),
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                latency_ms=latency_ms,
            ):
                fence_lost()
                return
            if not pending:
                break

            messages.append({
                "role": "assistant", "content": call_text or None,
                "tool_calls": [
                    {"id": tc.id or f"call_{call_seq}_{i}", "type": "function",
                     "function": {"name": tc.name,
                                  "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                    for i, tc in enumerate(pending)
                ],
            })
            for i, tc in enumerate(pending):
                call_seq += 1
                allowed = tc.name in plan.allowed_tools
                tool = deps.tools.get(tc.name)
                t0 = time.monotonic()
                if tool is None or not allowed:
                    result = ToolResult(
                        content=json.dumps({"error": "unknown_tool" if tool is None else "tool_not_allowed"},
                                           ensure_ascii=False),
                        result_ref={"tool": tc.name, "allowed": allowed},
                    )
                else:
                    try:
                        result = tool.execute(tc.arguments, tool_ctx)
                    except Exception as exc:
                        log.warning("turn_id=%s tool=%s error=%s", turn_id, tc.name, exc)
                        result = ToolResult(content=json.dumps({"error": str(exc)}, ensure_ascii=False),
                                            result_ref={"tool": tc.name, "error": str(exc)})
                if not repo.append_tool_call(
                    turn_id, owner, fence, call_seq=call_seq, tool_name=tc.name,
                    args=tc.arguments, result_ref=result.result_ref,
                    latency_ms=int((time.monotonic() - t0) * 1000), was_allowed=allowed,
                ):
                    fence_lost()
                    return
                if result.snippet_refs and not repo.add_context_snippets(turn_id, owner, fence, result.snippet_refs):
                    fence_lost()
                    return
                if result.answer_event and not repo.append_answer_event(turn_id, owner, fence, **result.answer_event):
                    fence_lost()
                    return
                publish("tool", {"name": tc.name, "arguments": tc.arguments, "was_allowed": allowed})
                messages.append({"role": "tool", "tool_call_id": tc.id or f"call_{call_seq}_{i}",
                                 "content": result.content})

        if not repo.finalize_turn(turn_id, owner, fence, status="completed", content=accumulated):
            fence_lost()
            return
        log.info("turn_id=%s completed calls=%s chars=%s", turn_id, call_seq, len(accumulated))
        publish("done", {"turn_id": turn_id, "status": "completed"})

    except ModelError as exc:
        log.warning("turn_id=%s model_error=%s", turn_id, exc)
        fail("model_error")
    except asyncio.CancelledError:
        interrupt("drain_timeout")
        raise
    except Exception:
        log.exception("turn_id=%s internal_error", turn_id)
        fail("internal_error")
