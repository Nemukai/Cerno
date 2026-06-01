from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from cerno.config import EmbeddingProcessingConfig, Settings
from cerno.models import Document, DocumentChunk, DocumentChunkSearchRow, DocumentPage
from cerno.services.document_search import (
    chunk_document_pages,
    fuse_hybrid_results,
    reciprocal_rank_fusion,
    retrieve_document_chunks,
)


def test_clause_aware_chunking_preserves_section_numbers_and_heading_path() -> None:
    document = _document()
    page = _page(
        """
# Agreement

## Payment Terms

Section 4 Fees

4.1 The buyer shall pay the invoice amount within thirty days of receiving the invoice.
The amount due includes all taxes and charges described in this document.

4.2 Late fees apply when payment is not received by the due date stated above.
""".strip()
    )

    chunks = chunk_document_pages(
        document,
        [page],
        config=EmbeddingProcessingConfig(chunk_min_chars=1),
    )

    clause = next(chunk for chunk in chunks if chunk.clause_no == "4.1")
    assert clause.section_no == "4"
    assert clause.heading_path == ("Agreement", "Payment Terms", "Section 4: Fees")
    assert clause.start_char < clause.end_char
    assert "Context: Contract.pdf > Agreement > Payment Terms > Section 4: Fees" in clause.text


def test_rrf_fusion_ranks_doc_that_wins_both_lists_highest() -> None:
    rows = [_row("a", 0), _row("b", 1), _row("c", 2)]

    scores = reciprocal_rank_fusion([["a", "b"], ["a", "c"]])
    fused = fuse_hybrid_results(
        bm25_rows=[rows[0], rows[1]],
        vector_rows=[rows[0], rows[2]],
        top_n=3,
    )

    assert scores["a"] > scores["b"]
    assert scores["a"] > scores["c"]
    assert fused[0].chunk_id == "a"


def test_retrieval_filters_by_session_org_and_user() -> None:
    provider = FakeEmbeddingProvider()
    store = FakeSearchStore(
        [
            _row("same-session", 0, session_id="session-1", organization_id="org-1"),
            _row("other-session", 1, session_id="session-2", organization_id="org-1"),
        ]
    )

    hits = asyncio.run(
        retrieve_document_chunks(
            query="payment terms",
            session_id="session-1",
            organization_id="org-1",
            user_id="user-1",
            chunk_store=store,
            embedding_provider=provider,
            settings=Settings(_env_file=None),
            top_n=5,
        )
    )

    assert [hit.chunk_id for hit in hits] == ["same-session"]
    assert store.calls == [
        ("bm25", "session-1", "org-1", "user-1"),
        ("vector", "session-1", "org-1", "user-1"),
    ]


def test_low_confidence_propagates_page_to_chunk() -> None:
    chunks = chunk_document_pages(
        _document(),
        [_page("Section 1 Review\n\n1.1 [illegible] payment terms", low_confidence=True)],
        config=EmbeddingProcessingConfig(chunk_min_chars=1),
    )

    assert chunks
    assert all(chunk.low_confidence for chunk in chunks)


class FakeEmbeddingProvider:
    name = "fake"
    model = "fake-embedding"
    dimension = 3

    async def embed_texts(
        self,
        texts: Sequence[str],
        *,
        llm_client: object | None = None,
    ) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class FakeSearchStore:
    def __init__(self, rows: Sequence[DocumentChunkSearchRow]) -> None:
        self.rows = list(rows)
        self.calls: list[tuple[str, str, str, str]] = []

    def search_bm25(
        self,
        *,
        query: str,
        session_id: str,
        organization_id: str,
        user_id: str,
        limit: int,
    ) -> list[DocumentChunkSearchRow]:
        self.calls.append(("bm25", session_id, organization_id, user_id))
        return self._filtered(session_id, organization_id)[:limit]

    def search_vector(
        self,
        *,
        embedding: Sequence[float],
        session_id: str,
        organization_id: str,
        user_id: str,
        limit: int,
    ) -> list[DocumentChunkSearchRow]:
        self.calls.append(("vector", session_id, organization_id, user_id))
        return self._filtered(session_id, organization_id)[:limit]

    def _filtered(self, session_id: str, organization_id: str) -> list[DocumentChunkSearchRow]:
        return [
            row
            for row in self.rows
            if row.chunk.session_id == session_id and row.chunk.organization_id == organization_id
        ]


def _document() -> Document:
    return Document(
        id="doc-1",
        session_id="session-1",
        user_id="user-1",
        organization_id="org-1",
        filename="Contract.pdf",
        content_hash="hash",
        page_count=1,
        status="processed",
        created_at=datetime.now(UTC),
    )


def _page(markdown: str, *, low_confidence: bool = False) -> DocumentPage:
    return DocumentPage(
        id="page-1",
        document_id="doc-1",
        page_number=1,
        source="text_layer",
        markdown=markdown,
        char_count=len(markdown),
        quality_score=0.9,
        low_confidence=low_confidence,
    )


def _row(
    chunk_id: str,
    chunk_index: int,
    *,
    session_id: str = "session-1",
    organization_id: str = "org-1",
) -> DocumentChunkSearchRow:
    return DocumentChunkSearchRow(
        chunk=DocumentChunk(
            id=chunk_id,
            document_id="doc-1",
            session_id=session_id,
            organization_id=organization_id,
            chunk_index=chunk_index,
            text=f"chunk {chunk_id}",
            heading_path=["Agreement"],
            section_no="1",
            clause_no=None,
            page_number=1,
            start_char=0,
            end_char=20,
            low_confidence=False,
            created_at=datetime.now(UTC),
        ),
        document_filename="Contract.pdf",
        rank=1.0,
    )
