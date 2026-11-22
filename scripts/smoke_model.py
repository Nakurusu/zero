"""Real-API smoke: streaming deltas, tool call round-trip, usage (D-model/H6/B7)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.config import Settings
from agent.model_client import Delta, Finish, OpenAICompatClient, ToolCall, Usage

QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_material",
        "description": "按关键词查询预置学习材料片段，返回匹配片段的内容。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "查询关键词"}},
            "required": ["query"],
        },
    },
}


async def main() -> None:
    settings = Settings.from_env()
    if not settings.api_key:
        print("API_KEY missing (.env)")
        sys.exit(2)
    client = OpenAICompatClient(settings)
    print(f"model={settings.model_params.model} base_url={settings.base_url}")
    print(f"params={settings.model_params.as_dict()}")

    print("\n--- 1) plain streaming ---")
    text, usage, finish = "", None, None
    async for ev in client.stream_chat([
        {"role": "user", "content": "用一句话介绍一元二次方程的判别式。"},
    ]):
        if isinstance(ev, Delta):
            text += ev.content
        elif isinstance(ev, Usage):
            usage = ev
        elif isinstance(ev, Finish):
            finish = ev
    assert text.strip(), "no deltas"
    assert usage is not None, "usage missing (include_usage)"
    print(text)
    print(f"usage={usage} finish={finish}")

    print("\n--- 2) tool call round-trip ---")
    messages = [
        {"role": "system", "content": "你是数学学习助手。回答前必须先调用 query_material 查询相关材料。"},
        {"role": "user", "content": "韦达定理的内容是什么？"},
    ]
    calls: list[ToolCall] = []
    text1 = ""
    async for ev in client.stream_chat(messages, tools=[QUERY_TOOL]):
        if isinstance(ev, Delta):
            text1 += ev.content
        elif isinstance(ev, ToolCall):
            calls.append(ev)
    assert calls, "model did not issue a tool call"
    tc = calls[0]
    print(f"tool_call: {tc.name}({tc.arguments})")
    messages.append({
        "role": "assistant", "content": text1 or None,
        "tool_calls": [{"id": tc.id, "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}],
    })
    messages.append({
        "role": "tool", "tool_call_id": tc.id,
        "content": json.dumps({"snippets": [{"title": "韦达定理", "content": "若 x₁、x₂ 是 ax²+bx+c=0 的两根，则 x₁+x₂=−b/a，x₁·x₂=c/a。"}]}, ensure_ascii=False),
    })
    text2, usage2 = "", None
    async for ev in client.stream_chat(messages, tools=[QUERY_TOOL]):
        if isinstance(ev, Delta):
            text2 += ev.content
        elif isinstance(ev, Usage):
            usage2 = ev
    assert text2.strip(), "no continuation after tool result"
    print(text2)
    print(f"usage={usage2}")

    await client.close()
    print("\nMODEL SMOKE PASSED")


if __name__ == "__main__":
    asyncio.run(main())
