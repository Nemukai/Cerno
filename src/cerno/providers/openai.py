from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from cerno.config import Settings
from cerno.llm import LLMClient, LLMError
from cerno.providers.protocols import OCRPageResult

OCR_PROMPT = """Transcribe this document page verbatim into GitHub-flavored Markdown.
Preserve tables, headings, clause numbers, section numbers, form labels, and reading order.
Mark unreadable spans as [illegible]. Do not invent missing content.
Return strict JSON matching the requested schema."""

OCR_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "markdown",
        "self_confidence",
        "illegible_regions",
        "has_tables",
        "layout_notes",
    ],
    "properties": {
        "markdown": {"type": "string"},
        "self_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "illegible_regions": {"type": "array", "items": {"type": "string"}},
        "has_tables": {"type": "boolean"},
        "layout_notes": {"type": "string"},
    },
}

@dataclass(frozen=True)
class OpenAIOCREngine:
    settings: Settings
    model: str
    name: str = "openai"

    async def extract_text(
        self,
        *,
        content: bytes,
        mime_type: str | None = None,
        filename: str | None = None,
    ) -> str:
        result = await self.extract_page_image(
            image=content,
            mime_type=mime_type or "image/png",
            filename=filename,
        )
        return result.markdown

    async def extract_page_image(
        self,
        *,
        image: bytes,
        mime_type: str = "image/png",
        filename: str | None = None,
        llm_client: LLMClient | None = None,
    ) -> OCRPageResult:
        client = llm_client or LLMClient(self.settings)
        image_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await client.respond(
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": f"Filename: {filename or 'document-page.png'}",
                                },
                                {"type": "input_image", "image_url": image_url},
                            ],
                        }
                    ],
                    instructions=OCR_PROMPT,
                    model=self.model,
                    reasoning_effort="low",
                    reasoning_summary="auto",
                    response_format={
                        "type": "json_schema",
                        "name": "ocr_page_result",
                        "schema": OCR_RESPONSE_SCHEMA,
                        "strict": True,
                    },
                )
                return _parse_ocr_response(response.raw)
            except Exception as exc:
                last_error = exc
        raise LLMError(f"OCR failed after retry: {last_error}") from last_error


@dataclass(frozen=True)
class OpenAIEmbeddingProvider:
    settings: Settings
    model: str
    dimension: int
    name: str = "openai"

    async def embed_texts(
        self,
        texts: Sequence[str],
        *,
        llm_client: LLMClient | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        client = llm_client or LLMClient(self.settings)
        return await client.embed_texts(
            texts=list(texts),
            model=self.model,
            dimensions=self.dimension,
        )


def _parse_ocr_response(raw: dict[str, Any]) -> OCRPageResult:
    status = raw.get("status")
    if status not in {None, "completed"}:
        raise LLMError(f"OCR response did not complete: status={status}")
    for item in raw.get("output", []) or []:
        if isinstance(item, dict) and item.get("type") == "refusal":
            raise LLMError("OCR model refused the page")
    text = _output_text(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"OCR returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMError("OCR returned non-object JSON")
    confidence = float(payload.get("self_confidence") or 0.0)
    regions = payload.get("illegible_regions")
    return OCRPageResult(
        markdown=str(payload.get("markdown") or ""),
        self_confidence=max(0.0, min(1.0, confidence)),
        illegible_regions=tuple(str(item) for item in regions if isinstance(item, str))
        if isinstance(regions, list)
        else (),
        has_tables=bool(payload.get("has_tables")),
        layout_notes=str(payload.get("layout_notes") or ""),
    )


def _output_text(raw: dict[str, Any]) -> str:
    if isinstance(raw.get("output_text"), str):
        return str(raw["output_text"])
    parts: list[str] = []
    for item in raw.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n".join(parts)
