from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from cerno.config import Settings

DEFAULT_MAX_LLM_CALLS = 5


class LLMError(RuntimeError):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMToolError(LLMError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise LLMToolError(f"unknown tool: {name}")
        return self._tools[name]

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


class HTTPTransport(Protocol):
    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> httpx.Response: ...


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        transport: HTTPTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport or _HttpxTransport(settings.llm_timeout_seconds)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.llm_provider == "openrouter":
            headers["HTTP-Referer"] = self.settings.llm_site_url
            headers["X-Title"] = self.settings.llm_app_name
        return headers

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        response = await self._call_with_retry(url, body)
        return _parse_response(response)

    async def _call_with_retry(
        self, url: str, body: dict[str, Any], max_retries: int = 2
    ) -> dict[str, Any]:
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = await self._transport.post(url, json=body, headers=self._headers())
                if response.status_code == 429:
                    raise LLMRateLimitError(f"rate limited: {response.text}")
                if response.status_code >= 400:
                    raise LLMError(f"llm error {response.status_code}: {response.text}")
                parsed: dict[str, Any] = response.json()
                return parsed
            except LLMRateLimitError as exc:
                last_error = exc
                if attempt == max_retries:
                    break
                await asyncio.sleep(delay)
                delay *= 2
            except httpx.HTTPError as exc:
                raise LLMError(f"http error: {exc}") from exc
        assert last_error is not None
        raise last_error


class _HttpxTransport:
    def __init__(self, timeout_seconds: float) -> None:
        self._timeout = timeout_seconds

    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(url, json=json, headers=headers)


def _parse_response(raw: dict[str, Any]) -> LLMResponse:
    choice = raw["choices"][0]
    message = choice["message"]
    content = message.get("content") or ""
    finish_reason = choice.get("finish_reason") or "stop"
    tool_calls_raw = message.get("tool_calls") or []
    tool_calls = [
        ToolCall(
            id=tc["id"],
            name=tc["function"]["name"],
            arguments=_parse_args(tc["function"].get("arguments", "{}")),
        )
        for tc in tool_calls_raw
    ]
    return LLMResponse(content=content, tool_calls=tool_calls, finish_reason=finish_reason, raw=raw)


def _parse_args(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    parsed: dict[str, Any] = json.loads(raw)
    return parsed


@dataclass
class ToolTurn:
    call: ToolCall
    result: dict[str, Any]


@dataclass
class LoopResult:
    final_message: str
    tool_turns: list[ToolTurn]
    finish_reason: str
    call_count: int


async def run_tool_loop(
    *,
    client: LLMClient,
    registry: ToolRegistry,
    messages: list[dict[str, Any]],
    max_calls: int = DEFAULT_MAX_LLM_CALLS,
    on_message: Callable[[dict[str, Any]], None] | None = None,
) -> LoopResult:
    tool_turns: list[ToolTurn] = []
    current_messages = list(messages)
    tools = registry.schemas()
    call_count = 0

    while True:
        if call_count >= max_calls:
            return LoopResult(
                final_message="(aborted: max llm calls reached)",
                tool_turns=tool_turns,
                finish_reason="max_calls",
                call_count=call_count,
            )

        response = await client.complete(messages=current_messages, tools=tools)
        call_count += 1

        assistant_message: dict[str, Any] = {"role": "assistant", "content": response.content}
        if response.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in response.tool_calls
            ]
        current_messages.append(assistant_message)
        if on_message is not None:
            on_message(assistant_message)

        if not response.tool_calls:
            return LoopResult(
                final_message=response.content,
                tool_turns=tool_turns,
                finish_reason=response.finish_reason,
                call_count=call_count,
            )

        for call in response.tool_calls:
            tool = registry.get(call.name)
            try:
                result = await tool.handler(call.arguments)
            except LLMToolError:
                raise
            except Exception as exc:
                result = {"error": str(exc)}
            tool_turns.append(ToolTurn(call=call, result=result))
            tool_message = {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result),
            }
            current_messages.append(tool_message)
            if on_message is not None:
                on_message(tool_message)
