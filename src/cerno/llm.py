from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from cerno.config import Settings
from cerno.repositories import LLMUsageRepository

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
    strict: bool = False

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
        if self.strict:
            schema["strict"] = True
        return schema


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
        return [t.to_schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    response_id: str | None = None
    reasoning_summary: str | None = None
    usage_total_tokens: int = 0
    status: str = "completed"
    raw: dict[str, Any] = field(default_factory=dict)


class HTTPTransport(Protocol):
    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response: ...


def _reasoning_block(effort: str | None, summary: str | None) -> dict[str, Any] | None:
    if not effort or effort.lower() in {"off", "none", ""}:
        return None
    block: dict[str, Any] = {"effort": effort}
    if summary and summary.lower() not in {"off", "none", ""}:
        block["summary"] = summary
    return block


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        transport: HTTPTransport | None = None,
        usage_repo: LLMUsageRepository | None = None,
        user_id: str | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport or _HttpxTransport(settings.llm_timeout_seconds)
        self._usage_repo = usage_repo
        self._user_id = user_id

    def with_usage(self, usage_repo: LLMUsageRepository, user_id: str) -> LLMClient:
        return LLMClient(
            settings=self.settings,
            transport=self._transport,
            usage_repo=usage_repo,
            user_id=user_id,
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.llm_provider == "openrouter":
            headers["HTTP-Referer"] = self.settings.llm_site_url
            headers["X-Title"] = self.settings.llm_app_name
        return headers

    async def respond(
        self,
        *,
        input: list[dict[str, Any]] | str,
        instructions: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        previous_response_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        reasoning_summary: str | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        store: bool = True,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "input": input,
            "store": store,
        }
        if instructions is not None:
            body["instructions"] = instructions
        if previous_response_id is not None:
            body["previous_response_id"] = previous_response_id
        if tools:
            body["tools"] = tools
        block = _reasoning_block(
            reasoning_effort or self.settings.llm_reasoning_effort,
            reasoning_summary or self.settings.llm_reasoning_summary,
        )
        if block is not None:
            body["reasoning"] = block
        if temperature is not None:
            body["temperature"] = temperature
        if response_format is not None:
            body["text"] = {"format": response_format}

        url = f"{self.settings.llm_base_url.rstrip('/')}/responses"
        self._ensure_token_budget()
        response = await self._call_with_retry(url, body)
        parsed = _parse_response(response)
        self._record_token_usage(parsed.usage_total_tokens)
        return parsed

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        reasoning_summary: str | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Convenience: split system messages into `instructions`, pass the rest
        as `input` items for the Responses API."""
        instructions_parts: list[str] = []
        input_items: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                if isinstance(content, str) and content:
                    instructions_parts.append(content)
                continue
            if role in ("user", "assistant"):
                input_items.append({"role": role, "content": content})
        instructions = "\n\n".join(instructions_parts) if instructions_parts else None
        return await self.respond(
            input=input_items,
            instructions=instructions,
            tools=tools,
            model=model,
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
            temperature=temperature,
        )

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

    def _usage_day(self) -> str:
        return datetime.now(UTC).date().isoformat()

    def _ensure_token_budget(self) -> None:
        if self._usage_repo is None or self._user_id is None:
            return
        if self._usage_repo.get(self._user_id, self._usage_day()) >= self.settings.daily_token_cap:
            raise LLMRateLimitError("daily LLM token cap exceeded")

    def _record_token_usage(self, tokens: int) -> None:
        if tokens <= 0 or self._usage_repo is None or self._user_id is None:
            return
        self._usage_repo.add_tokens(self._user_id, self._usage_day(), tokens)


class _HttpxTransport:
    def __init__(self, timeout_seconds: float) -> None:
        self._timeout = timeout_seconds

    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(url, json=json, headers=headers)


def _parse_response(raw: dict[str, Any]) -> LLMResponse:
    output = raw.get("output", []) or []
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    reasoning_parts: list[str] = []
    for item in output:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content", []) or []:
                if part.get("type") == "output_text":
                    text_parts.append(part.get("text", ""))
        elif kind == "function_call":
            tool_calls.append(
                ToolCall(
                    call_id=item.get("call_id") or item.get("id", ""),
                    name=item.get("name", ""),
                    arguments=_parse_args(item.get("arguments", "{}")),
                )
            )
        elif kind == "reasoning":
            for part in item.get("summary", []) or []:
                if part.get("type") == "summary_text":
                    reasoning_parts.append(part.get("text", ""))
    return LLMResponse(
        content="".join(text_parts),
        tool_calls=tool_calls,
        response_id=raw.get("id"),
        reasoning_summary="\n".join(reasoning_parts) if reasoning_parts else None,
        usage_total_tokens=_parse_total_tokens(raw.get("usage")),
        status=raw.get("status") or "completed",
        raw=raw,
    )


def _parse_total_tokens(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, int):
        return total
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        return input_tokens + output_tokens
    return 0


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
    response_id: str | None = None


async def run_tool_loop(
    *,
    client: LLMClient,
    registry: ToolRegistry,
    input: list[dict[str, Any]],
    instructions: str | None = None,
    reasoning_effort: str | None = None,
    reasoning_summary: str | None = None,
    max_calls: int = DEFAULT_MAX_LLM_CALLS,
    on_message: Callable[[dict[str, Any]], None] | None = None,
) -> LoopResult:
    """Run the tool-use loop against the Responses API.

    The first turn sends `input` + `instructions` + `tools`. Subsequent turns
    chain via `previous_response_id` and only ship the new
    `function_call_output` items so reasoning state persists on the server.
    """
    tool_turns: list[ToolTurn] = []
    tools = registry.schemas()
    pending_input: list[dict[str, Any]] = list(input)
    previous_response_id: str | None = None
    call_count = 0

    while True:
        if call_count >= max_calls:
            return LoopResult(
                final_message="(aborted: max llm calls reached)",
                tool_turns=tool_turns,
                finish_reason="max_calls",
                call_count=call_count,
                response_id=previous_response_id,
            )

        response = await client.respond(
            input=pending_input,
            tools=tools,
            instructions=instructions if previous_response_id is None else None,
            previous_response_id=previous_response_id,
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
        )
        call_count += 1
        previous_response_id = response.response_id

        if response.tool_calls:
            if on_message is not None:
                on_message(
                    {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": [
                            {
                                "id": tc.call_id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in response.tool_calls
                        ],
                    }
                )

            pending_input = []
            for call in response.tool_calls:
                tool = registry.get(call.name)
                try:
                    result = await tool.handler(call.arguments)
                except LLMToolError:
                    raise
                except Exception as exc:
                    result = {"error": str(exc)}
                tool_turns.append(ToolTurn(call=call, result=result))
                payload = json.dumps(result)
                pending_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": payload,
                    }
                )
                if on_message is not None:
                    on_message(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "content": payload,
                        }
                    )
            continue

        if on_message is not None:
            on_message({"role": "assistant", "content": response.content})
        return LoopResult(
            final_message=response.content,
            tool_turns=tool_turns,
            finish_reason=response.status,
            call_count=call_count,
            response_id=previous_response_id,
        )
