from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from cerno.config import EmbeddingProcessingConfig, Settings
from cerno.llm import LLMClient
from cerno.models import Document, DocumentChunk, DocumentChunkSearchRow, DocumentPage
from cerno.providers import EmbeddingProvider
from cerno.repositories import DocumentChunkRepository

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SECTION_RE = re.compile(
    r"^(?:(section|sec\.?|chapter|part)\s+)([A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*)\b[:.)\-\s]*(.*)$",
    re.IGNORECASE,
)
CLAUSE_RE = re.compile(r"^((?:\d+|[A-Z])(?:\.\d+){1,6})\b[.)\-\s]*(.*)$")


@dataclass(frozen=True)
class DocumentChunkDraft:
    document_id: str
    session_id: str
    organization_id: str
    chunk_index: int
    text: str
    heading_path: tuple[str, ...]
    section_no: str | None
    clause_no: str | None
    page_number: int
    start_char: int
    end_char: int
    low_confidence: bool


@dataclass(frozen=True)
class DocumentRetrievalHit:
    chunk_id: str
    document_id: str
    document_filename: str
    text: str
    page_number: int
    heading_path: tuple[str, ...]
    section_no: str | None
    clause_no: str | None
    low_confidence: bool
    score: float
    citation: str


class DocumentChunkSearchStore(Protocol):
    def search_bm25(
        self,
        *,
        query: str,
        session_id: str,
        organization_id: str,
        user_id: str,
        limit: int,
    ) -> list[DocumentChunkSearchRow]: ...

    def search_vector(
        self,
        *,
        embedding: Sequence[float],
        session_id: str,
        organization_id: str,
        user_id: str,
        limit: int,
    ) -> list[DocumentChunkSearchRow]: ...


class ChunkReranker(Protocol):
    async def rerank(
        self,
        query: str,
        hits: Sequence[DocumentRetrievalHit],
    ) -> list[DocumentRetrievalHit]: ...


class NoopReranker:
    async def rerank(
        self,
        query: str,
        hits: Sequence[DocumentRetrievalHit],
    ) -> list[DocumentRetrievalHit]:
        return list(hits)


@dataclass(frozen=True)
class _Block:
    text: str
    start: int
    end: int


def chunk_document_pages(
    document: Document,
    pages: Sequence[DocumentPage],
    *,
    config: EmbeddingProcessingConfig,
) -> list[DocumentChunkDraft]:
    chunks: list[DocumentChunkDraft] = []
    heading_stack: list[str] = []
    section_heading: str | None = None
    section_no: str | None = None

    for page in sorted(pages, key=lambda item: item.page_number):
        current_parts: list[str] = []
        current_start = 0
        current_end = 0
        current_heading_path: tuple[str, ...] = ()
        current_section_no: str | None = None
        current_clause_no: str | None = None

        def flush(
            *,
            current_page_number: int = page.page_number,
            current_low_confidence: bool = page.low_confidence,
        ) -> None:
            nonlocal current_parts
            nonlocal current_start
            nonlocal current_end
            nonlocal current_heading_path
            nonlocal current_section_no
            nonlocal current_clause_no
            if not current_parts:
                return
            body = "\n\n".join(part.strip() for part in current_parts if part.strip()).strip()
            if not body:
                current_parts = []
                return
            context_parts = [document.filename, *current_heading_path]
            text = f"Context: {' > '.join(context_parts)}\n\n{body}"
            chunks.append(
                DocumentChunkDraft(
                    document_id=document.id,
                    session_id=document.session_id,
                    organization_id=document.organization_id,
                    chunk_index=len(chunks),
                    text=text,
                    heading_path=current_heading_path,
                    section_no=current_section_no,
                    clause_no=current_clause_no,
                    page_number=current_page_number,
                    start_char=current_start,
                    end_char=current_end,
                    low_confidence=current_low_confidence,
                )
            )
            current_parts = []
            current_start = 0
            current_end = 0
            current_heading_path = ()
            current_section_no = None
            current_clause_no = None

        for block in _markdown_blocks(page.markdown):
            heading = _markdown_heading(block.text)
            if heading is not None:
                flush()
                level, title = heading
                heading_stack = [*heading_stack[: level - 1], title]
                section_heading = None
                section_no = None

            section = _section_heading(block.text)
            clause_no = _clause_number(block.text)
            starts_new_unit = heading is not None or section is not None or clause_no is not None
            if starts_new_unit:
                flush()
            if section is not None:
                section_no, section_heading = section
                clause_no = None

            heading_path = tuple(
                item for item in [*heading_stack, section_heading] if item is not None
            )
            block_section_no = section_no
            block_clause_no = clause_no

            if len(block.text) > config.chunk_max_chars:
                flush()
                for split in _split_block(block, config.chunk_max_chars):
                    current_parts = [split.text]
                    current_start = split.start
                    current_end = split.end
                    current_heading_path = heading_path
                    current_section_no = block_section_no
                    current_clause_no = block_clause_no
                    flush()
                continue

            projected = sum(len(part) for part in current_parts) + len(block.text)
            if current_parts and projected > config.chunk_max_chars:
                flush()
            if not current_parts:
                current_start = block.start
                current_heading_path = heading_path
                current_section_no = block_section_no
                current_clause_no = block_clause_no
            current_parts.append(block.text)
            current_end = block.end
            if sum(len(part) for part in current_parts) >= config.chunk_max_chars:
                flush()

        flush()

    return _merge_tiny_chunks(chunks, config.chunk_min_chars)


async def index_document_chunks(
    *,
    document: Document,
    pages: Sequence[DocumentPage],
    chunk_repo: DocumentChunkRepository,
    embedding_provider: EmbeddingProvider,
    settings: Settings,
    llm_client: LLMClient | None = None,
) -> list[DocumentChunk]:
    drafts = chunk_document_pages(
        document,
        pages,
        config=settings.processing.embedding,
    )
    chunk_repo.delete_for_document(document.id)
    if not drafts:
        return []
    embeddings: list[list[float]] = []
    for batch in _batches(drafts, settings.processing.embedding.batch_size):
        vectors = await embedding_provider.embed_texts(
            [draft.text for draft in batch],
            llm_client=llm_client,
        )
        if len(vectors) != len(batch):
            raise ValueError("embedding provider returned a different number of vectors")
        embeddings.extend(vectors)
    stored: list[DocumentChunk] = []
    for draft, embedding in zip(drafts, embeddings, strict=True):
        if len(embedding) != embedding_provider.dimension:
            raise ValueError(
                f"embedding dimension mismatch: expected {embedding_provider.dimension}, got {len(embedding)}"
            )
        stored.append(
            chunk_repo.add(
                document_id=draft.document_id,
                session_id=draft.session_id,
                organization_id=draft.organization_id,
                chunk_index=draft.chunk_index,
                text=draft.text,
                heading_path=draft.heading_path,
                section_no=draft.section_no,
                clause_no=draft.clause_no,
                page_number=draft.page_number,
                start_char=draft.start_char,
                end_char=draft.end_char,
                low_confidence=draft.low_confidence,
                embedding=embedding,
            )
        )
    return stored


async def retrieve_document_chunks(
    *,
    query: str,
    session_id: str,
    organization_id: str,
    user_id: str,
    chunk_store: DocumentChunkSearchStore,
    embedding_provider: EmbeddingProvider,
    settings: Settings,
    llm_client: LLMClient | None = None,
    top_n: int = 8,
    reranker: ChunkReranker | None = None,
) -> list[DocumentRetrievalHit]:
    normalized_query = query.strip()
    if not normalized_query:
        return []
    query_vectors = await embedding_provider.embed_texts(
        [normalized_query],
        llm_client=llm_client,
    )
    if len(query_vectors) != 1:
        raise ValueError("embedding provider did not return one query vector")
    limit = max(top_n * 4, top_n)
    bm25_rows = chunk_store.search_bm25(
        query=normalized_query,
        session_id=session_id,
        organization_id=organization_id,
        user_id=user_id,
        limit=limit,
    )
    vector_rows = chunk_store.search_vector(
        embedding=query_vectors[0],
        session_id=session_id,
        organization_id=organization_id,
        user_id=user_id,
        limit=limit,
    )
    hits = fuse_hybrid_results(
        bm25_rows=bm25_rows,
        vector_rows=vector_rows,
        top_n=top_n,
    )
    return await (reranker or NoopReranker()).rerank(normalized_query, hits)


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    *,
    k: int = 60,
) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for index, item_id in enumerate(ranking, start=1):
            scores[item_id] += 1.0 / (k + index)
    return dict(scores)


def fuse_hybrid_results(
    *,
    bm25_rows: Sequence[DocumentChunkSearchRow],
    vector_rows: Sequence[DocumentChunkSearchRow],
    top_n: int,
    rrf_k: int = 60,
) -> list[DocumentRetrievalHit]:
    rows_by_id: dict[str, DocumentChunkSearchRow] = {}
    for row in [*bm25_rows, *vector_rows]:
        rows_by_id.setdefault(row.chunk.id, row)
    scores = reciprocal_rank_fusion(
        [
            [row.chunk.id for row in bm25_rows],
            [row.chunk.id for row in vector_rows],
        ],
        k=rrf_k,
    )
    ranked_ids = sorted(
        scores,
        key=lambda chunk_id: (-scores[chunk_id], rows_by_id[chunk_id].chunk.chunk_index),
    )
    return [_to_hit(rows_by_id[chunk_id], scores[chunk_id]) for chunk_id in ranked_ids[:top_n]]


def _to_hit(row: DocumentChunkSearchRow, score: float) -> DocumentRetrievalHit:
    chunk = row.chunk
    return DocumentRetrievalHit(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        document_filename=row.document_filename,
        text=chunk.text,
        page_number=chunk.page_number,
        heading_path=tuple(chunk.heading_path),
        section_no=chunk.section_no,
        clause_no=chunk.clause_no,
        low_confidence=chunk.low_confidence,
        score=score,
        citation=_citation(row),
    )


def _citation(row: DocumentChunkSearchRow) -> str:
    chunk = row.chunk
    parts = [row.document_filename, f"page {chunk.page_number}"]
    if chunk.section_no:
        parts.append(f"section {chunk.section_no}")
    if chunk.clause_no:
        parts.append(f"clause {chunk.clause_no}")
    return ", ".join(parts)


def _markdown_blocks(markdown: str) -> list[_Block]:
    blocks: list[_Block] = []
    for match in re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", markdown, flags=re.DOTALL):
        text = match.group(0).strip()
        if text:
            blocks.append(_Block(text=text, start=match.start(), end=match.end()))
    return blocks


def _markdown_heading(text: str) -> tuple[int, str] | None:
    first_line = text.splitlines()[0].strip()
    match = HEADING_RE.match(first_line)
    if not match:
        return None
    return len(match.group(1)), _clean_heading(match.group(2))


def _section_heading(text: str) -> tuple[str, str] | None:
    first_line = text.splitlines()[0].strip()
    match = SECTION_RE.match(first_line)
    if not match:
        return None
    kind = match.group(1).rstrip(".").title()
    number = match.group(2)
    title = match.group(3).strip(" :-.)")
    heading = f"{kind} {number}" + (f": {title}" if title else "")
    return number, heading


def _clause_number(text: str) -> str | None:
    first_line = text.splitlines()[0].strip()
    match = CLAUSE_RE.match(first_line)
    return match.group(1) if match else None


def _clean_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" #")).strip()


def _split_block(block: _Block, max_chars: int) -> list[_Block]:
    chunks: list[_Block] = []
    offset = 0
    while offset < len(block.text):
        end = min(len(block.text), offset + max_chars)
        if end < len(block.text):
            space = block.text.rfind(" ", offset, end)
            if space > offset:
                end = space
        text = block.text[offset:end].strip()
        if text:
            chunks.append(
                _Block(
                    text=text,
                    start=block.start + offset,
                    end=block.start + end,
                )
            )
        offset = max(end, offset + 1)
    return chunks


def _merge_tiny_chunks(
    chunks: list[DocumentChunkDraft],
    min_chars: int,
) -> list[DocumentChunkDraft]:
    if not chunks:
        return []
    merged: list[DocumentChunkDraft] = []
    pending: DocumentChunkDraft | None = None
    for chunk in chunks:
        if pending is None:
            pending = chunk
            continue
        same_page = pending.page_number == chunk.page_number
        same_confidence = pending.low_confidence == chunk.low_confidence
        if len(pending.text) < min_chars and same_page and same_confidence:
            pending = DocumentChunkDraft(
                document_id=pending.document_id,
                session_id=pending.session_id,
                organization_id=pending.organization_id,
                chunk_index=pending.chunk_index,
                text=f"{pending.text}\n\n{chunk.text}",
                heading_path=chunk.heading_path or pending.heading_path,
                section_no=chunk.section_no or pending.section_no,
                clause_no=chunk.clause_no or pending.clause_no,
                page_number=pending.page_number,
                start_char=pending.start_char,
                end_char=chunk.end_char,
                low_confidence=pending.low_confidence,
            )
            continue
        merged.append(pending)
        pending = chunk
    if pending is not None:
        merged.append(pending)
    return [
        DocumentChunkDraft(
            document_id=chunk.document_id,
            session_id=chunk.session_id,
            organization_id=chunk.organization_id,
            chunk_index=index,
            text=chunk.text,
            heading_path=chunk.heading_path,
            section_no=chunk.section_no,
            clause_no=chunk.clause_no,
            page_number=chunk.page_number,
            start_char=chunk.start_char,
            end_char=chunk.end_char,
            low_confidence=chunk.low_confidence,
        )
        for index, chunk in enumerate(merged)
    ]


def _batches(
    chunks: Sequence[DocumentChunkDraft],
    batch_size: int,
) -> list[Sequence[DocumentChunkDraft]]:
    return [chunks[index : index + batch_size] for index in range(0, len(chunks), batch_size)]
