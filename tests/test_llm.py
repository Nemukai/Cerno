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


def _http(body: dict[str, Any], status: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "http://test")
    return httpx.Response(status_code=status, json=body, request=request)


def _responses_body(
    *,
    response_id: str = "resp_test",
    text: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_summary: str | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    if reasoning_summary is not None:
        output.append(
            {
                "id": "rs_1",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": reasoning_summary}],
            }
        )
    if tool_calls:
        for tc in tool_calls:
            output.append(
                {
                    "id": f"fc_{tc['call_id']}",
                    "type": "function_call",
                    "call_id": tc["call_id"],
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                    "status": "completed",
                }
            )
    if text is not None:
        output.append(
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        )
    return {
        "id": response_id,
        "model": "test-model",
        "status": status,
        "output": output,
    }


def _make_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    root = tmp_path_factory.mktemp("llm")
    return Settings(
        data_root=root,
        llm_api_key="test-key",
        llm_base_url="https://example.test/api/v1",
        llm_model="test-model",
        llm_reasoning_effort="medium",
        llm_reasoning_summary="auto",
    )


async def test_respond_hits_responses_endpoint_with_reasoning(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = _make_settings(tmp_path_factory)
    transport = FakeTransport([_http(_responses_body(text="hello"))])
    client = LLMClient(settings=settings, transport=transport)

    response = await client.respond(
        input=[{"role": "user", "content": "hi"}],
        instructions="you are cerno",
    )
    assert response.content == "hello"
    assert response.response_id == "resp_test"
    assert response.tool_calls == []

    req = transport.requests[0]
    assert req["url"].endswith("/responses")
    body = req["body"]
    assert body["model"] == "test-model"
    assert body["instructions"] == "you are cerno"
    assert body["input"] == [{"role": "user", "content": "hi"}]
    assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert "tools" not in body


async def test_respond_omits_reasoning_when_off(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = _make_settings(tmp_path_factory)
    transport = FakeTransport([_http(_responses_body(text="ok"))])
    client = LLMClient(settings=settings, transport=transport)

    await client.respond(input="hi", reasoning_effort="off")
    body = transport.requests[0]["body"]
    assert "reasoning" not in body


async def test_complete_splits_system_into_instructions(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = _make_settings(tmp_path_factory)
    transport = FakeTransport([_http(_responses_body(text="ok"))])
    client = LLMClient(settings=settings, transport=transport)

    await client.complete(
        messages=[
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
    )
    body = transport.requests[0]["body"]
    assert body["instructions"] == "be terse"
    assert body["input"] == [{"role": "user", "content": "hi"}]


async def test_tool_loop_uses_previous_response_id(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = _make_settings(tmp_path_factory)

    first = _responses_body(
        response_id="resp_1",
        tool_calls=[
            {"call_id": "c1", "name": "echo", "arguments": json.dumps({"text": "hi"})}
        ],
    )
    second = _responses_body(response_id="resp_2", text="echoed: hi")
    transport = FakeTransport([_http(first), _http(second)])
    client = LLMClient(settings=settings, transport=transport)

    registry = ToolRegistry()
    observed_args: list[dict[str, Any]] = []

    async def echo_handler(args: dict[str, Any]) -> dict[str, Any]:
        observed_args.append(args)
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
        input=[{"role": "user", "content": "say hi"}],
        instructions="tool-use",
        reasoning_effort="medium",
    )

    assert result.final_message == "echoed: hi"
    assert result.call_count == 2
    assert result.response_id == "resp_2"
    assert result.tool_turns[0].result == {"echoed": "hi"}
    assert observed_args == [{"text": "hi"}]

    first_body = transport.requests[0]["body"]
    assert first_body["instructions"] == "tool-use"
    assert "previous_response_id" not in first_body
    assert first_body["tools"][0] == {
        "type": "function",
        "name": "echo",
        "description": "echo back",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }

    second_body = transport.requests[1]["body"]
    assert second_body["previous_response_id"] == "resp_1"
    assert "instructions" not in second_body
    assert second_body["input"] == [
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": json.dumps({"echoed": "hi"}),
        }
    ]


async def test_tool_loop_respects_max_calls(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = _make_settings(tmp_path_factory)
    never_stop = _responses_body(
        tool_calls=[
            {"call_id": "c", "name": "spin", "arguments": "{}"}
        ]
    )
    transport = FakeTransport([_http(never_stop)] * 10)
    client = LLMClient(settings=settings, transport=transport)

    registry = ToolRegistry()

    async def spin_handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    registry.register(
        Tool(
            name="spin",
            description="spin",
            parameters={"type": "object"},
            handler=spin_handler,
        )
    )

    result = await run_tool_loop(
        client=client, registry=registry, input=[], max_calls=2
    )
    assert result.finish_reason == "max_calls"
    assert result.call_count == 2


async def test_rate_limit_raises(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = _make_settings(tmp_path_factory)
    transport = FakeTransport(
        [
            _http({"error": "rate"}, status=429),
            _http({"error": "rate"}, status=429),
            _http({"error": "rate"}, status=429),
        ]
    )
    client = LLMClient(settings=settings, transport=transport)
    with pytest.raises(LLMRateLimitError):
        await client.respond(input="hi")


async def test_http_error_wrapped(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = _make_settings(tmp_path_factory)
    transport = FakeTransport([_http({"error": "boom"}, status=500)])
    client = LLMClient(settings=settings, transport=transport)
    with pytest.raises(LLMError):
        await client.respond(input="hi")


def test_registry_prevents_duplicate_registration() -> None:
    registry = ToolRegistry()

    async def noop(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    tool = Tool(
        name="x", description="", parameters={"type": "object"}, handler=noop
    )
    registry.register(tool)
    with pytest.raises(ValueError):
        registry.register(tool)


def test_registry_schema_is_flat_responses_shape() -> None:
    registry = ToolRegistry()

    async def noop(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    registry.register(
        Tool(
            name="run_sql",
            description="Run SQL",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
            handler=noop,
            strict=True,
        )
    )
    schemas = registry.schemas()
    assert schemas[0] == {
        "type": "function",
        "name": "run_sql",
        "description": "Run SQL",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        "strict": True,
    }


async def test_parse_reasoning_summary(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = _make_settings(tmp_path_factory)
    body = _responses_body(text="final", reasoning_summary="thinking about it")
    transport = FakeTransport([_http(body)])
    client = LLMClient(settings=settings, transport=transport)

    response = await client.respond(input="hi")
    assert response.reasoning_summary == "thinking about it"
