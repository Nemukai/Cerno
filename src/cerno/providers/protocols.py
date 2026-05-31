from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class OCREngine(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def extract_text(
        self,
        *,
        content: bytes,
        mime_type: str | None = None,
        filename: str | None = None,
    ) -> str: ...


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...
