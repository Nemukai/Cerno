from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cerno.config import Settings
from cerno.llm import (
    LLMClient,
    LLMError,
    LLMRateLimitError,
    Tool,
    ToolRegistry,
    run_tool_loop,
)


class FakeTransport:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        self.requests.append({"url": url, "body": json, "headers": headers})
        return self._responses.pop(0)


def _response(body: dict[str, Any], status: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "http://test")
    return httpx.Response(status_code=status, json=body, request=request)


def _assistant_body(content: str = "", tool_calls: list[dict[str, Any]] | None = None, finish: str = "stop") -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish,
            }
        ]
    }


def _make_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    root = tmp_path_factory.mktemp("llm")
    return Settings(
        data_root=root,
        llm_api_key="test-key",
        llm_base_url="https://example.test/api/v1",
        llm_model="test-model",
    )


async def test_complete_plain_message(tmp_path_factory: pytest.TempPathFactory) -> None:
    settings = _make_settings(tmp_path_factory)
    transport = FakeTransport([_response(_assistant_body(content="hello"))])
    client = LLMClient(settings=settings, transport=transport)

    response = await client.complete(messages=[{"role": "user", "content": "hi"}])
    assert response.content == "hello"
    assert response.tool_calls == []
    body = transport.requests[0]["body"]
    assert body["model"] == "test-model"
    assert "tools" not in body


async def test_tool_loop_dispatches_and_loops(tmp_path_factory: pytest.TempPathFactory) -> None:
    settings = _make_settings(tmp_path_factory)

    tool_response = _assistant_body(
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "echo", "arguments": json.dumps({"text": "hi"})},
            }
        ],
        finish="tool_calls",
    )
    final_response = _assistant_body(content="echoed: hi")
    transport = FakeTransport([_response(tool_response), _response(final_response)])
    client = LLMClient(settings=settings, transport=transport)

    registry = ToolRegistry()
    calls: list[dict[str, Any]] = []

    async def echo_handler(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"echoed": args["text"]}

    registry.register(
        Tool(
            name="echo",
            description="echo back",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo_handler,
        )
    )

    result = await run_tool_loop(
        client=client,
        registry=registry,
        messages=[{"role": "user", "content": "say hi"}],
    )
    assert result.final_message == "echoed: hi"
    assert result.call_count == 2
    assert len(result.tool_turns) == 1
    assert result.tool_turns[0].result == {"echoed": "hi"}
    assert calls == [{"text": "hi"}]


async def test_tool_loop_respects_max_calls(tmp_path_factory: pytest.TempPathFactory) -> None:
    settings = _make_settings(tmp_path_factory)
    never_stop = _assistant_body(
        tool_calls=[
            {
                "id": "c",
                "type": "function",
                "function": {"name": "spin", "arguments": "{}"},
            }
        ],
        finish="tool_calls",
    )
    transport = FakeTransport([_response(never_stop)] * 10)
    client = LLMClient(settings=settings, transport=transport)

    registry = ToolRegistry()

    async def spin_handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    registry.register(
        Tool(name="spin", description="spin", parameters={"type": "object"}, handler=spin_handler)
    )

    result = await run_tool_loop(
        client=client, registry=registry, messages=[], max_calls=2
    )
    assert result.finish_reason == "max_calls"
    assert result.call_count == 2


async def test_rate_limit_raises(tmp_path_factory: pytest.TempPathFactory) -> None:
    settings = _make_settings(tmp_path_factory)
    transport = FakeTransport([
        _response({"error": "rate"}, status=429),
        _response({"error": "rate"}, status=429),
        _response({"error": "rate"}, status=429),
    ])
    client = LLMClient(settings=settings, transport=transport)
    with pytest.raises(LLMRateLimitError):
        await client.complete(messages=[])


async def test_http_error_wrapped(tmp_path_factory: pytest.TempPathFactory) -> None:
    settings = _make_settings(tmp_path_factory)
    transport = FakeTransport([_response({"error": "boom"}, status=500)])
    client = LLMClient(settings=settings, transport=transport)
    with pytest.raises(LLMError):
        await client.complete(messages=[])


def test_registry_prevents_duplicate_registration() -> None:
    registry = ToolRegistry()

    async def noop(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    tool = Tool(name="x", description="", parameters={"type": "object"}, handler=noop)
    registry.register(tool)
    with pytest.raises(ValueError):
        registry.register(tool)


def test_registry_openai_schema_shape() -> None:
    registry = ToolRegistry()

    async def noop(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    registry.register(
        Tool(
            name="run_sql",
            description="Run SQL",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            handler=noop,
        )
    )
    schemas = registry.schemas()
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "run_sql"
    assert schemas[0]["function"]["parameters"]["properties"]["query"]["type"] == "string"
