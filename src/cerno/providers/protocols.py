from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from cerno.llm import LLMClient


@dataclass(frozen=True)
class OCRPageResult:
    markdown: str
    self_confidence: float
    illegible_regions: tuple[str, ...] = ()
    has_tables: bool = False
    layout_notes: str = ""


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

    async def extract_page_image(
        self,
        *,
        image: bytes,
        mime_type: str = "image/png",
        filename: str | None = None,
        llm_client: LLMClient | None = None,
    ) -> OCRPageResult: ...


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_texts(
        self,
        texts: Sequence[str],
        *,
        llm_client: LLMClient | None = None,
    ) -> list[list[float]]: ...
