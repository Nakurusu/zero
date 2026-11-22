"""Capture one real model tool-call round-trip as auditable evidence (B7).

Runs a real turn against the real API, exports the full turn audit plus the
SSE event log to eval/evidence/tool_call_example.json and a readable trace
summary to tool_call_example.md. Re-runnable; overwrites the evidence files.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.channel import DeltaChannel
from agent.config import Settings
from agent.executor import ExecutorDeps, run_turn
from agent.model_client import OpenAICompatClient
from agent.slot import TurnSlot
from agent.storage import Repo, load_seed
from agent.strategies import default_registry
from agent.tools import default_tool_registry

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "evidence"
PROMPT = "我想学习韦达定理。请先用 query_material 查询材料中关于韦达定理的内容，再讲解并出一道题。"


async def main() -> None:
    settings = Settings.from_env()
    assert settings.api_key, "API_KEY missing (.env)"
    db = Path(tempfile.mkdtemp()) / "evidence.db"
    repo = Repo(db)
    load_seed(repo, ROOT / "seeds" / "seed.json", settings.seed_hmac_key)
    instance = "inst-evidence"
    repo.heartbeat(instance)
    deps = ExecutorDeps(repo, settings, default_registry(), default_tool_registry(),
                        OpenAICompatClient(settings), DeltaChannel(), instance)
    slot = TurnSlot(repo, settings)
    conv = repo.create_conversation("t-quadratic", "u-demo", "explain-first", 1)
    result = await slot.claim(conv["id"], PROMPT, instance, "explain-first", 1,
                              client_msg_id="evidence-1")
    turn = result["turn"]
    deps.channel.publish(turn["id"], "turn_started",
                         {"turn_id": turn["id"], "turn_seq": turn["turn_seq"]})
    task = asyncio.create_task(run_turn(deps, turn))
    events = []
    async for ev in deps.channel.subscribe(turn["id"]):
        events.append({"offset": ev.offset, "type": ev.type, "data": ev.data})
    await task

    row = repo.get_turn(turn["id"])
    assert row["status"] == "completed", row["status"]
    model_calls = repo.get_model_calls(turn["id"])
    tool_calls = repo.get_tool_calls(turn["id"])
    assert tool_calls, "model issued no tool call; re-run"
    assert len(model_calls) >= 2, "expected continuation model call after tool result"

    messages = repo.turn_messages(turn["id"])
    trace = sorted(
        [{"call_seq": c["call_seq"], "kind": "model", "model_id": c["model_id"],
          "input_tokens": c["input_tokens"], "output_tokens": c["output_tokens"],
          "latency_ms": c["latency_ms"]} for c in model_calls]
        + [{"call_seq": c["call_seq"], "kind": "tool", "tool_name": c["tool_name"],
            "args": json.loads(c["args"]),
            "result_ref": json.loads(c["result_ref"]) if c["result_ref"] else None,
            "was_allowed": bool(c["was_allowed"]), "latency_ms": c["latency_ms"]} for c in tool_calls],
        key=lambda x: x["call_seq"],
    )
    export = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.model_params.model,
        "params": settings.model_params.as_dict(),
        "base_url": settings.base_url,
        "conversation": conv,
        "user_message": PROMPT,
        "turn": {k: row[k] for k in ("id", "turn_seq", "status", "planned_strategy", "planned_version",
                                      "actual_strategy", "actual_version", "fallback_reason",
                                      "started_at", "finished_at")},
        "plan_doc": json.loads(row["plan_doc"]),
        "messages": messages,
        "model_calls": [{**c, "params": json.loads(c["params"])} for c in model_calls],
        "tool_calls": [{**c, "args": json.loads(c["args"]),
                        "result_ref": json.loads(c["result_ref"]) if c["result_ref"] else None,
                        "was_allowed": bool(c["was_allowed"])} for c in tool_calls],
        "turn_context": repo.get_turn_context(turn["id"]),
        "trace": trace,
        "sse_events": events,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tool_call_example.json").write_text(
        json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")

    reply = next(m["content"] for m in messages if m["role"] == "assistant")
    lines = [
        "# 真实工具调用运行示例（B7）",
        "",
        f"- 捕获时间：{export['captured_at']}",
        f"- 模型：`{export['model']}`，参数：`{json.dumps(export['params'], ensure_ascii=False)}`",
        f"- 端点：`{export['base_url']}`",
        f"- 策略：{row['actual_strategy']}@{row['actual_version']}（planned={row['planned_strategy']}@{row['planned_version']}，fallback={row['fallback_reason']}）",
        f"- 用户消息：{PROMPT}",
        "",
        "## 执行轨迹（call_seq 跨模型/工具统一单调）",
        "",
        "| call_seq | 类型 | 名称/模型 | 关键信息 |",
        "|---|---|---|---|",
    ]
    for item in trace:
        if item["kind"] == "model":
            info = f"input={item['input_tokens']} output={item['output_tokens']} tokens, {item['latency_ms']}ms"
            lines.append(f"| {item['call_seq']} | model | {item['model_id']} | {info} |")
        else:
            args = json.dumps(item["args"], ensure_ascii=False)
            lines.append(f"| {item['call_seq']} | tool | {item['tool_name']} | args={args} allowed={item['was_allowed']} |")
    lines += [
        "",
        "模型发起工具调用 → 执行器回填工具结果 → 模型继续生成，直至最终回复（见 trace 中 model/tool 交错）。",
        "工具取回的材料片段 refs 已累积入 turn_context（审计覆盖模型实际所见）。",
        "",
        "## 最终回复",
        "",
        reply,
        "",
        "完整审计（plan_doc、model_calls、tool_calls、turn_context、SSE 事件）见 `tool_call_example.json`。",
        "复跑：`python scripts/capture_tool_example.py`。",
        "",
    ]
    (OUT / "tool_call_example.md").write_text("\n".join(lines), encoding="utf-8")
    await deps.model.close()
    repo.close()
    print(f"evidence written: {OUT}")
    print(f"trace: {[(t['call_seq'], t['kind'], t.get('tool_name', t.get('model_id'))) for t in trace]}")


if __name__ == "__main__":
    asyncio.run(main())
