from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAIOCREngine:
    model: str
    name: str = "openai"

    async def extract_text(
        self,
        *,
        content: bytes,
        mime_type: str | None = None,
        filename: str | None = None,
    ) -> str:
        raise NotImplementedError("OpenAI OCR is registered but not consumed by ingest yet")


@dataclass(frozen=True)
class OpenAIEmbeddingProvider:
    model: str
    name: str = "openai"

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError("OpenAI embeddings are registered but not consumed yet")
