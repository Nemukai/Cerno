from __future__ import annotations

import asyncio

from cerno.config import OCRProcessingConfig
from cerno.providers import OCRPageResult
from cerno.services.document_ingest import (
    page_cap_result,
    route_text_layer_or_ocr,
    score_page_quality,
)


def test_garbled_text_layer_falls_through_to_ocr() -> None:
    render_calls: list[str] = []
    ocr_calls: list[bytes] = []

    def render_initial(_: int) -> bytes:
        render_calls.append("initial")
        return b"initial-image"

    def render_retry(_: int) -> bytes:
        render_calls.append("retry")
        return b"retry-image"

    async def ocr_page(image: bytes) -> OCRPageResult:
        ocr_calls.append(image)
        return OCRPageResult(
            markdown="The invoice amount and payment date are clearly visible.",
            self_confidence=0.99,
            has_tables=False,
        )

    draft = asyncio.run(
        route_text_layer_or_ocr(
            page_number=1,
            text="Ã¿Ã¿ ��� 123 @@ ##",
            render_initial=render_initial,
            render_retry=render_retry,
            ocr_page=ocr_page,
            config=OCRProcessingConfig(),
        )
    )

    assert draft.source == "ocr"
    assert draft.low_confidence is False
    assert render_calls == ["initial"]
    assert ocr_calls == [b"initial-image"]


def test_quality_score_drops_for_illegible_density_and_low_dictionary_ratio() -> None:
    good = score_page_quality(
        "The invoice amount and payment date are listed in this document.",
        self_confidence=0.9,
        has_tables=False,
        threshold=0.72,
    )
    bad = score_page_quality(
        "[illegible] [illegible] [illegible] xqzv qwrty zzz999 !!!",
        self_confidence=0.9,
        has_tables=False,
        threshold=0.72,
    )

    assert bad.score < good.score
    assert "high_illegible_density" in bad.reasons
    assert "low_dictionary_word_ratio" in bad.reasons


def test_escalation_fires_once_only_for_low_confidence_pages() -> None:
    render_calls: list[str] = []
    ocr_calls: list[bytes] = []

    def render_initial(_: int) -> bytes:
        render_calls.append("initial")
        return b"initial-image"

    def render_retry(_: int) -> bytes:
        render_calls.append("retry")
        return b"retry-image"

    async def ocr_page(image: bytes) -> OCRPageResult:
        ocr_calls.append(image)
        return OCRPageResult(
            markdown="[illegible] xqzv qwrty zzz999",
            self_confidence=0.1,
            has_tables=False,
        )

    draft = asyncio.run(
        route_text_layer_or_ocr(
            page_number=1,
            text="",
            render_initial=render_initial,
            render_retry=render_retry,
            ocr_page=ocr_page,
            config=OCRProcessingConfig(),
        )
    )

    assert draft.source == "ocr_retry"
    assert draft.low_confidence is True
    assert render_calls == ["initial", "retry"]
    assert ocr_calls == [b"initial-image", b"retry-image"]


def test_page_cap_truncates_and_warns() -> None:
    result = page_cap_result(page_count=250, max_pages=200)

    assert result.limit == 200
    assert result.truncated is True
    assert result.warning_message == "document has 250 pages; ingesting first 200"
