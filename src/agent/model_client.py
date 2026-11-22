from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AsyncIterator

from openai import AsyncOpenAI, APIError, APIConnectionError, APITimeoutError

from .config import Settings


class ModelError(Exception):
    """Upstream model API failure; maps to turn failed(model_error)."""


@dataclass
class Delta:
    content: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class Finish:
    reason: str  # stop | tool_calls | length


Event = Delta | ToolCall | Usage | Finish


class OpenAICompatClient:
    """ModelClient seam implementation: OpenAI-compatible streaming adapter.

    Yields normalized events: Delta* -> ToolCall* -> Usage? -> Finish.
    Model id and params come from the single config source (E2); strategies
    never override them.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.api_key, base_url=settings.base_url, timeout=120.0,
        )

    async def stream_chat(
        self, messages: list[dict], tools: list[dict] | None = None,
    ) -> AsyncIterator[Event]:
        p = self.settings.model_params
        kwargs: dict = {
            "model": p.model,
            "messages": messages,
            "temperature": p.temperature,
            "max_tokens": p.max_tokens,
            "top_p": p.top_p,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        tool_buf: dict[int, dict] = {}
        finish_reason = "stop"
        usage: Usage | None = None
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if delta is None:
                    continue
                if delta.content:
                    yield Delta(delta.content)
                for tc in delta.tool_calls or []:
                    buf = tool_buf.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        buf["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            buf["name"] += tc.function.name
                        if tc.function.arguments:
                            buf["arguments"] += tc.function.arguments
        except (APIError, APIConnectionError, APITimeoutError) as exc:
            raise ModelError(str(exc)) from exc

        for buf in tool_buf.values():
            try:
                args = json.loads(buf["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": buf["arguments"]}
            yield ToolCall(buf["id"], buf["name"], args)
        if usage is not None:
            yield usage
        yield Finish(finish_reason)

    async def close(self) -> None:
        await self._client.close()
