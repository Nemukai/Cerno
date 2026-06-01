from __future__ import annotations

import math
import re
import tempfile
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from struct import pack
from typing import Any, Protocol, cast

import numpy as np
from charset_normalizer import from_bytes

from cerno.config import OCRProcessingConfig, Settings
from cerno.llm import LLMClient
from cerno.models import Document, DocumentPage, DocumentPageSource, SourceAsset
from cerno.providers import OCREngine, OCRPageResult
from cerno.repositories import (
    AssetArtifactRepository,
    DocumentRepository,
    SourceAssetRepository,
    WorkspaceAssetRepository,
    new_id,
)
from cerno.services.ingest import hash_file
from cerno.storage import ObjectStore, document_page_image_key, source_object_key

PDF_SUFFIXES = {".pdf"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
SUPPORTED_DOCUMENT_SUFFIXES = PDF_SUFFIXES | TEXT_SUFFIXES

COMMON_WORDS = {
    "a",
    "about",
    "account",
    "all",
    "amount",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "date",
    "document",
    "for",
    "from",
    "has",
    "in",
    "invoice",
    "is",
    "it",
    "name",
    "no",
    "not",
    "of",
    "on",
    "or",
    "page",
    "payment",
    "section",
    "shall",
    "tax",
    "term",
    "that",
    "the",
    "this",
    "to",
    "total",
    "value",
    "with",
}

ProgressCallback = Callable[[str, str, str, int, dict[str, object] | None], None]
RenderPage = Callable[[int], bytes]
OCRPage = Callable[[bytes], Awaitable[OCRPageResult]]


class DocumentIngestError(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentPageDraft:
    page_number: int
    source: DocumentPageSource
    markdown: str
    quality_score: float
    low_confidence: bool
    quality_reasons: list[str]
    image: bytes | None = None
    retry_image: bool = False


@dataclass(frozen=True)
class QualityResult:
    score: float
    reasons: list[str]
    dictionary_ratio: float
    illegible_density: float
    alphanumeric_ratio: float
    non_ascii_ratio: float


@dataclass(frozen=True)
class TextLayerGuardResult:
    ok: bool
    reasons: list[str]
    dictionary_ratio: float
    non_ascii_ratio: float
    char_count: int


@dataclass(frozen=True)
class PageCapResult:
    limit: int
    truncated: bool
    warning_message: str | None = None


@dataclass(frozen=True)
class IngestedDocument:
    document: Document
    pages: list[DocumentPage]
    duplicate: bool = False
    truncated: bool = False


class _PdfPage(Protocol):
    def get_width(self) -> float: ...
    def get_height(self) -> float: ...
    def get_textpage(self) -> Any: ...
    def render(self, *, scale: float) -> Any: ...
    def close(self) -> None: ...


def is_supported_document(filename: str, mime_type: str | None = None) -> bool:
    suffix = Path(filename).suffix.lower()
    if suffix in SUPPORTED_DOCUMENT_SUFFIXES:
        return True
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    return normalized in {"application/pdf", "text/plain", "text/markdown"}


def garbled_text_guard(text: str, config: OCRProcessingConfig) -> TextLayerGuardResult:
    normalized = _normalize_text(text)
    char_count = len(normalized)
    dictionary_ratio = _dictionary_ratio(normalized)
    non_ascii_ratio = _non_ascii_ratio(normalized)
    reasons: list[str] = []
    if char_count < config.text_layer_min_chars:
        reasons.append("too_few_characters")
    if dictionary_ratio < config.text_layer_min_dictionary_ratio:
        reasons.append("low_dictionary_word_ratio")
    if non_ascii_ratio > config.text_layer_max_non_ascii_ratio:
        reasons.append("high_non_ascii_ratio")
    if _alphanumeric_ratio(normalized) < 0.25:
        reasons.append("low_alphanumeric_ratio")
    return TextLayerGuardResult(
        ok=not reasons,
        reasons=reasons,
        dictionary_ratio=dictionary_ratio,
        non_ascii_ratio=non_ascii_ratio,
        char_count=char_count,
    )


def score_page_quality(
    markdown: str,
    *,
    self_confidence: float,
    has_tables: bool,
    threshold: float,
) -> QualityResult:
    normalized = _normalize_text(markdown)
    dictionary_ratio = _dictionary_ratio(normalized)
    illegible_density = _illegible_density(normalized)
    alphanumeric_ratio = _alphanumeric_ratio(normalized)
    non_ascii_ratio = _non_ascii_ratio(normalized)
    table_penalty = _table_variance_penalty(markdown) if has_tables else 0.0
    score = (
        0.52 * max(0.0, min(1.0, self_confidence))
        + 0.20 * dictionary_ratio
        + 0.16 * alphanumeric_ratio
        + 0.12 * (1.0 - illegible_density)
        - table_penalty
    )
    score = max(0.0, min(1.0, score))
    reasons: list[str] = []
    if dictionary_ratio < 0.12:
        reasons.append("low_dictionary_word_ratio")
    if illegible_density > 0.04:
        reasons.append("high_illegible_density")
    if alphanumeric_ratio < 0.25:
        reasons.append("low_alphanumeric_ratio")
    if non_ascii_ratio > 0.4:
        reasons.append("high_non_ascii_ratio")
    if table_penalty > 0:
        reasons.append("unstable_table_columns")
    if score < threshold:
        reasons.append("below_quality_threshold")
    return QualityResult(
        score=score,
        reasons=reasons,
        dictionary_ratio=dictionary_ratio,
        illegible_density=illegible_density,
        alphanumeric_ratio=alphanumeric_ratio,
        non_ascii_ratio=non_ascii_ratio,
    )


async def ocr_with_bounded_escalation(
    *,
    page_number: int,
    render_initial: RenderPage,
    render_retry: RenderPage,
    ocr_page: OCRPage,
    threshold: float,
) -> DocumentPageDraft:
    image = render_initial(page_number)
    result = await ocr_page(image)
    quality = score_page_quality(
        result.markdown,
        self_confidence=result.self_confidence,
        has_tables=result.has_tables,
        threshold=threshold,
    )
    if quality.score >= threshold:
        return DocumentPageDraft(
            page_number=page_number,
            source="ocr",
            markdown=result.markdown,
            quality_score=quality.score,
            low_confidence=False,
            quality_reasons=quality.reasons,
        )

    retry_image = render_retry(page_number)
    retry_result = await ocr_page(retry_image)
    retry_quality = score_page_quality(
        retry_result.markdown,
        self_confidence=retry_result.self_confidence,
        has_tables=retry_result.has_tables,
        threshold=threshold,
    )
    return DocumentPageDraft(
        page_number=page_number,
        source="ocr_retry",
        markdown=retry_result.markdown,
        quality_score=retry_quality.score,
        low_confidence=retry_quality.score < threshold,
        quality_reasons=retry_quality.reasons,
        image=retry_image if retry_quality.score < threshold else None,
        retry_image=True,
    )


async def route_text_layer_or_ocr(
    *,
    page_number: int,
    text: str,
    render_initial: RenderPage,
    render_retry: RenderPage,
    ocr_page: OCRPage,
    config: OCRProcessingConfig,
) -> DocumentPageDraft:
    guard = garbled_text_guard(text, config)
    if guard.ok:
        return DocumentPageDraft(
            page_number=page_number,
            source="text_layer",
            markdown=_normalize_text(text),
            quality_score=0.96,
            low_confidence=False,
            quality_reasons=[],
        )
    return await ocr_with_bounded_escalation(
        page_number=page_number,
        render_initial=render_initial,
        render_retry=render_retry,
        ocr_page=ocr_page,
        threshold=config.pdf_quality_threshold,
    )


def should_truncate_pages(page_count: int, max_pages: int) -> bool:
    return page_count > max_pages


def page_cap_result(page_count: int, max_pages: int) -> PageCapResult:
    limit = min(page_count, max_pages)
    if page_count <= max_pages:
        return PageCapResult(limit=limit, truncated=False)
    return PageCapResult(
        limit=limit,
        truncated=True,
        warning_message=f"document has {page_count} pages; ingesting first {limit}",
    )


async def ingest_document(
    *,
    source_path: Path,
    original_filename: str,
    original_content_type: str | None,
    original_size_bytes: int | None,
    user_id: str,
    organization_id: str,
    session_id: str,
    settings: Settings,
    documents_repo: DocumentRepository,
    source_assets_repo: SourceAssetRepository,
    workspace_assets_repo: WorkspaceAssetRepository,
    artifacts_repo: AssetArtifactRepository,
    object_store: ObjectStore,
    ocr_engine: OCREngine,
    llm_client: LLMClient | None = None,
    progress: ProgressCallback | None = None,
) -> IngestedDocument:
    if not source_path.exists():
        raise DocumentIngestError(f"source not found: {source_path}")
    if not is_supported_document(original_filename, original_content_type):
        raise DocumentIngestError(f"unsupported document type: {Path(original_filename).suffix}")

    content_hash = hash_file(source_path)
    existing = documents_repo.find_by_hash(session_id, content_hash)
    if existing is not None:
        return IngestedDocument(
            document=existing,
            pages=documents_repo.list_pages(existing.id),
            duplicate=True,
        )

    source_asset = _ensure_source_asset(
        source_path=source_path,
        original_filename=original_filename,
        original_content_type=original_content_type,
        user_id=user_id,
        organization_id=organization_id,
        content_hash=content_hash,
        source_assets_repo=source_assets_repo,
        artifacts_repo=artifacts_repo,
        object_store=object_store,
    )
    if workspace_assets_repo.get(session_id, source_asset.id) is None:
        workspace_assets_repo.create(
            session_id=session_id,
            source_asset_id=source_asset.id,
            display_name=original_filename,
        )

    suffix = Path(original_filename).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        text = _decode_text_document(source_path)
        drafts = [
            DocumentPageDraft(
                page_number=1,
                source="text_layer",
                markdown=text,
                quality_score=0.98 if text.strip() else 0.2,
                low_confidence=not bool(text.strip()),
                quality_reasons=[] if text.strip() else ["empty_document"],
            )
        ]
        page_count = 1
        truncated = False
    else:
        _emit(progress, "reading_document", "reading_document", "reading PDF pages", 15)
        drafts, page_count, truncated = await _extract_pdf_pages(
            source_path=source_path,
            config=settings.processing.ocr,
            ocr_engine=ocr_engine,
            llm_client=llm_client,
            progress=progress,
        )

    document = documents_repo.create(
        session_id=session_id,
        user_id=user_id,
        organization_id=organization_id,
        filename=original_filename,
        content_hash=content_hash,
        page_count=page_count,
        status="processing",
    )
    pages: list[DocumentPage] = []
    for draft in drafts:
        image_object_key = None
        if draft.image is not None:
            image_object_key = _persist_low_confidence_image(
                image=draft.image,
                user_id=user_id,
                session_id=session_id,
                document_id=document.id,
                page_number=draft.page_number,
                retry=draft.retry_image,
                object_store=object_store,
            )
        pages.append(
            documents_repo.add_page(
                document_id=document.id,
                page_number=draft.page_number,
                source=draft.source,
                markdown=draft.markdown,
                char_count=len(draft.markdown),
                quality_score=draft.quality_score,
                low_confidence=draft.low_confidence,
                quality_reasons=draft.quality_reasons,
                image_object_key=image_object_key,
            )
        )
    documents_repo.set_status(document.id, "processed")
    processed = documents_repo.get(document.id) or document
    _emit(
        progress,
        "reading_document",
        "pages_read",
        f"read {len(pages)} document page(s)",
        92,
        {"page_count": len(pages), "truncated": truncated},
    )
    return IngestedDocument(
        document=processed,
        pages=pages,
        duplicate=False,
        truncated=truncated,
    )


async def _extract_pdf_pages(
    *,
    source_path: Path,
    config: OCRProcessingConfig,
    ocr_engine: OCREngine,
    llm_client: LLMClient | None,
    progress: ProgressCallback | None,
) -> tuple[list[DocumentPageDraft], int, bool]:
    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]
    except ImportError as exc:
        raise DocumentIngestError("pypdfium2 is required for PDF document ingestion") from exc

    pdf = pdfium.PdfDocument(str(source_path))
    try:
        page_count = len(pdf)
        cap = page_cap_result(page_count, config.pdf_max_pages_per_document)
        if cap.truncated:
            _emit(
                progress,
                "quality_review",
                "quality_review",
                cap.warning_message or "document page cap applied",
                20,
                {
                    "page_count": page_count,
                    "page_cap": config.pdf_max_pages_per_document,
                    "truncated": True,
                },
                level="warning",
            )
        drafts: list[DocumentPageDraft] = []
        for page_index in range(cap.limit):
            page = cast(_PdfPage, pdf[page_index])
            page_number = page_index + 1
            try:
                def render_initial(_: int, *, current_page: _PdfPage = page) -> bytes:
                    return _render_page_png(current_page, config.pdf_render_dpi, config)

                def render_retry(_: int, *, current_page: _PdfPage = page) -> bytes:
                    return _render_page_png(current_page, config.pdf_retry_render_dpi, config)

                async def ocr_current_page(
                    image: bytes,
                    *,
                    current_page_number: int = page_number,
                ) -> OCRPageResult:
                    return await ocr_engine.extract_page_image(
                        image=image,
                        mime_type="image/png",
                        filename=f"{source_path.name}-page-{current_page_number}.png",
                        llm_client=llm_client,
                    )

                text = _extract_page_text(page)
                guard = garbled_text_guard(text, config)
                if guard.ok:
                    draft = await route_text_layer_or_ocr(
                        page_number=page_number,
                        text=text,
                        render_initial=render_initial,
                        render_retry=render_retry,
                        ocr_page=ocr_current_page,
                        config=config,
                    )
                    drafts.append(draft)
                    _emit(
                        progress,
                        "reading_document",
                        "reading_document",
                        f"read text layer for page {page_number}",
                        _page_progress(page_number, cap.limit),
                        {"page_number": page_number, "source": "text_layer"},
                    )
                    continue
                _emit(
                    progress,
                    "ocr_page",
                    "ocr_page",
                    f"OCR page {page_number}",
                    _page_progress(page_number, cap.limit),
                    {"page_number": page_number, "text_guard_reasons": guard.reasons},
                )
                draft = await route_text_layer_or_ocr(
                    page_number=page_number,
                    text=text,
                    render_initial=render_initial,
                    render_retry=render_retry,
                    ocr_page=ocr_current_page,
                    config=config,
                )
                drafts.append(draft)
                _emit(
                    progress,
                    "quality_review",
                    "quality_review",
                    f"quality checked page {page_number}",
                    _page_progress(page_number, cap.limit),
                    {
                        "page_number": page_number,
                        "quality_score": draft.quality_score,
                        "low_confidence": draft.low_confidence,
                        "quality_reasons": draft.quality_reasons,
                    },
                    level="warning" if draft.low_confidence else "info",
                )
            finally:
                page.close()
        return drafts, page_count, cap.truncated
    finally:
        pdf.close()


def _extract_page_text(page: _PdfPage) -> str:
    textpage = page.get_textpage()
    try:
        return str(textpage.get_text_range())
    finally:
        textpage.close()


def _render_page_png(page: _PdfPage, dpi: int, config: OCRProcessingConfig) -> bytes:
    longest_points = max(page.get_width(), page.get_height())
    scale = min(dpi / 72.0, config.pdf_max_render_side / max(longest_points, 1.0))
    bitmap = page.render(scale=scale)
    try:
        return _bitmap_to_png(bitmap)
    finally:
        bitmap.close()


def _bitmap_to_png(bitmap: Any) -> bytes:
    arr = np.asarray(bitmap.to_numpy())
    if arr.ndim == 2:
        gray = arr.astype(np.uint8, copy=False)
        return _encode_png(gray.tobytes(), gray.shape[1], gray.shape[0], color_type=0)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise DocumentIngestError("PDF renderer returned unsupported bitmap shape")
    rgb = arr[:, :, [2, 1, 0]].astype(np.uint8, copy=False)
    return _encode_png(rgb.tobytes(), rgb.shape[1], rgb.shape[0], color_type=2)


def _encode_png(payload: bytes, width: int, height: int, *, color_type: int) -> bytes:
    channels = 1 if color_type == 0 else 3
    stride = width * channels
    rows = b"".join(b"\x00" + payload[i : i + stride] for i in range(0, len(payload), stride))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return pack(">I", len(data)) + kind + data + pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _decode_text_document(source_path: Path) -> str:
    payload = source_path.read_bytes()
    match = from_bytes(payload).best()
    if match is None:
        return payload.decode("utf-8")
    encoding = match.encoding or "utf-8"
    try:
        return payload.decode(encoding)
    except UnicodeDecodeError:
        return str(match)


def _ensure_source_asset(
    *,
    source_path: Path,
    original_filename: str,
    original_content_type: str | None,
    user_id: str,
    organization_id: str,
    content_hash: str,
    source_assets_repo: SourceAssetRepository,
    artifacts_repo: AssetArtifactRepository,
    object_store: ObjectStore,
) -> SourceAsset:
    source_asset = source_assets_repo.get_by_hash(
        user_id,
        content_hash,
        organization_id=organization_id,
    )
    if source_asset is not None:
        return source_asset
    asset_id = new_id()
    object_key = source_object_key(user_id, asset_id, content_hash, original_filename)
    stored = object_store.put_path(
        source_path,
        object_key,
        content_type=original_content_type or "application/octet-stream",
    )
    source_asset = source_assets_repo.create(
        user_id=user_id,
        organization_id=organization_id,
        sha256=content_hash,
        original_filename=original_filename,
        mime_type=original_content_type,
        size_bytes=stored.size_bytes,
        storage_backend=stored.backend,
        object_key=stored.object_key,
        asset_id=asset_id,
    )
    artifacts_repo.create(
        user_id=user_id,
        organization_id=organization_id,
        source_asset_id=source_asset.id,
        artifact_type="source",
        storage_backend=stored.backend,
        object_key=stored.object_key,
        content_hash=content_hash,
        size_bytes=stored.size_bytes,
        mime_type=original_content_type,
    )
    return source_asset


def _persist_low_confidence_image(
    *,
    image: bytes,
    user_id: str,
    session_id: str,
    document_id: str,
    page_number: int,
    retry: bool,
    object_store: ObjectStore,
) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(image)
        tmp_path = Path(tmp.name)
    try:
        key = document_page_image_key(
            user_id,
            session_id,
            document_id,
            page_number,
            retry=retry,
        )
        stored = object_store.put_path(tmp_path, key, content_type="image/png")
        return stored.object_key
    finally:
        tmp_path.unlink(missing_ok=True)


def _emit(
    progress: ProgressCallback | None,
    kind: str,
    step_key: str,
    message: str,
    percent: int,
    details: dict[str, object] | None = None,
    *,
    level: str = "info",
) -> None:
    if progress is None:
        return
    progress(kind, step_key, message, percent, {**(details or {}), "level": level})


def _page_progress(page_number: int, page_count: int) -> int:
    if page_count <= 0:
        return 30
    return min(95, 25 + round((page_number / page_count) * 65))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def _dictionary_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-z]{2,}", text.lower())
    if not words:
        return 0.0
    common = sum(1 for word in words if word in COMMON_WORDS)
    return common / len(words)


def _non_ascii_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for char in text if ord(char) > 127) / len(text)


def _alphanumeric_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for char in text if char.isalnum()) / len(text)


def _illegible_density(text: str) -> float:
    if not text:
        return 1.0
    return len(re.findall(r"\[illegible\]", text, flags=re.IGNORECASE)) * len("[illegible]") / len(text)


def _table_variance_penalty(markdown: str) -> float:
    counts = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if "|" not in stripped or set(stripped.replace("|", "").replace("-", "").strip()) <= {":"}:
            continue
        cells = [cell for cell in stripped.strip("|").split("|")]
        if len(cells) > 1:
            counts.append(len(cells))
    if len(counts) < 3:
        return 0.0
    mean = sum(counts) / len(counts)
    if mean <= 0:
        return 0.0
    variance = sum((count - mean) ** 2 for count in counts) / len(counts)
    coefficient = math.sqrt(variance) / mean
    return min(0.15, coefficient * 0.12)
