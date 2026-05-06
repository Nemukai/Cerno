from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from cerno.config import Settings
from cerno.repositories import LLMUsageRepository

DEFAULT_MAX_LLM_CALLS = 20


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
        conversation: str | None = None,
        context_management: list[dict[str, Any]] | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        store: bool = True,
    ) -> LLMResponse:
        body = self._response_body(
            input=input,
            instructions=instructions,
            tools=tools,
            previous_response_id=previous_response_id,
            model=model,
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
            temperature=temperature,
            response_format=response_format,
            conversation=conversation,
            context_management=context_management,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
            store=store,
        )
        url = f"{self.settings.llm_base_url.rstrip('/')}/responses"
        self._ensure_token_budget()
        response = await self._call_with_retry(url, body)
        parsed = _parse_response(response)

        self._record_token_usage(parsed.usage_total_tokens)
        return parsed

    async def create_conversation(self, *, metadata: dict[str, str] | None = None) -> str:
        url = f"{self.settings.llm_base_url.rstrip('/')}/conversations"
        body: dict[str, Any] = {}
        if metadata:
            body["metadata"] = metadata
        response = await self._call_with_retry(url, body)
        conversation_id = response.get("id")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise LLMError("OpenAI conversation response did not include an id")
        return conversation_id

    async def stream_response(
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
        conversation: str | None = None,
        context_management: list[dict[str, Any]] | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        store: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        body = self._response_body(
            input=input,
            instructions=instructions,
            tools=tools,
            previous_response_id=previous_response_id,
            model=model,
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
            temperature=temperature,
            response_format=response_format,
            conversation=conversation,
            context_management=context_management,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
            store=store,
        )
        body["stream"] = True
        body["stream_options"] = {"include_obfuscation": False}
        url = f"{self.settings.llm_base_url.rstrip('/')}/responses"
        self._ensure_token_budget()
        async for event in self._stream_sse(url, body):
            yield event

    def _response_body(
        self,
        *,
        input: list[dict[str, Any]] | str,
        instructions: str | None,
        tools: list[dict[str, Any]] | None,
        previous_response_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        reasoning_summary: str | None,
        temperature: float | None,
        response_format: dict[str, Any] | None,
        conversation: str | None,
        context_management: list[dict[str, Any]] | None,
        prompt_cache_key: str | None,
        prompt_cache_retention: str | None,
        store: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "input": input,
            "store": store,
        }
        if instructions is not None:
            body["instructions"] = instructions
        if previous_response_id is not None:
            body["previous_response_id"] = previous_response_id
        if conversation is not None:
            body["conversation"] = conversation
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
        if context_management is not None:
            body["context_management"] = context_management
        if prompt_cache_key is not None:
            body["prompt_cache_key"] = prompt_cache_key
        if prompt_cache_retention is not None:
            body["prompt_cache_retention"] = prompt_cache_retention
        return body

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
        self, url: str, body: dict[str, Any], max_retries: int = 3
    ) -> dict[str, Any]:
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = await self._transport.post(url, json=body, headers=self._headers())
                if response.status_code == 429:
                    raise LLMRateLimitError(f"rate limited: {response.text}")
                if response.status_code >= 500:
                    raise LLMError(f"llm server error {response.status_code}: {response.text}")
                if response.status_code >= 400:
                    raise LLMError(f"llm client error {response.status_code}: {response.text}")
                parsed: dict[str, Any] = response.json()
                return parsed
            except LLMError as exc:
                if "client error" in str(exc):
                    raise exc
                last_error = exc
            except Exception as exc:
                last_error = exc

            if attempt == max_retries:
                break
            await asyncio.sleep(delay)
            delay *= 2

        assert last_error is not None
        raise LLMError(f"http error after {max_retries} retries: {last_error}") from last_error

    async def _stream_sse(self, url: str, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        buffer: list[str] = []
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
            async with client.stream("POST", url, json=body, headers=self._headers()) as response:
                if response.status_code >= 400:
                    text = await response.aread()
                    raise LLMError(
                        f"llm stream error {response.status_code}: {text.decode(errors='replace')}"
                    )
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        buffer.append(data)
                        continue
                    if line:
                        continue
                    if not buffer:
                        continue
                    payload = "\n".join(buffer)
                    buffer = []
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    yield event

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


def conversation_context_options(session_id: str, turn_id: str) -> dict[str, Any]:
    return {
        "context_management": [{"type": "compaction", "compact_threshold": 200_000}],
        "prompt_cache_key": _prompt_cache_key(session_id, turn_id),
        "prompt_cache_retention": "24h",
    }


def _prompt_cache_key(session_id: str, turn_id: str) -> str:
    digest = hashlib.sha256(f"{session_id}:{turn_id}".encode()).hexdigest()[:32]
    return f"cerno:{digest}"


async def run_tool_loop(
    *,
    client: LLMClient,
    registry: ToolRegistry,
    input: list[dict[str, Any]],
    instructions: str | None = None,
    model: str | None = None,
    conversation: str | None = None,
    context_management: list[dict[str, Any]] | None = None,
    prompt_cache_key: str | None = None,
    prompt_cache_retention: str | None = None,
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
    use_conversation = conversation is not None

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
            instructions=instructions,
            previous_response_id=None if use_conversation else previous_response_id,
            model=model,
            conversation=conversation,
            context_management=context_management,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
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
