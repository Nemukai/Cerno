from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cerno.api.sessions import (
    _apply_minor_schema_corrections,
    _approval_payload_from_discovery,
    _build_discovery_response,
    _refresh_docs_from_approval,
    _schema_context_from_discovery,
)
from cerno.config import Settings
from cerno.db import DbConnection, connect
from cerno.llm import LLMResponse
from cerno.providers import OCRPageResult
from cerno.repositories import (
    AssetArtifactRepository,
    AuditRepository,
    DataDocRepository,
    DocumentChunkRepository,
    DocumentRepository,
    FileRepository,
    LinkRepository,
    OrganizationRepository,
    ProcessingEventRepository,
    SchemaRepository,
    SessionRepository,
    SourceAssetRepository,
    UserRepository,
    WorkspaceAssetRepository,
    WorkspaceTableRepository,
)
from cerno.services.document_ingest import ingest_document
from cerno.services.document_search import index_document_chunks, retrieve_document_chunks
from cerno.services.ingest import ingest_file
from cerno.services.reingest import apply_approval, preview_rows
from cerno.services.schema_corrections import (
    apply_operations_to_payload,
    interpret_schema_correction,
    minor_operations,
    selected_structural_operations,
)
from cerno.services.tools import ToolContext, build_tool_registry
from cerno.storage import ObjectStore, StoredObject

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer
    except Exception as exc:  # pragma: no cover - only hit in misconfigured dev envs
        pytest.skip(f"testcontainers is unavailable: {exc}")

    try:
        container = PostgresContainer("pgvector/pgvector:pg16")
        container.start()
    except Exception as exc:
        pytest.skip(f"pgvector Postgres container unavailable: {exc}")

    try:
        yield container.get_connection_url(driver=None)
    finally:
        container.stop()


def test_discovery_corrections_documents_rag_pipeline(
    postgres_url: str, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, postgres_url)
    conn = connect(settings)
    try:
        repos = _repos(conn)
        object_store = LocalObjectStore(tmp_path / "objects")
        user = repos.users.upsert_from_google(
            google_sub="integration-user",
            email="integration@example.com",
            name="Integration User",
            picture=None,
            email_approved=True,
        )
        org = repos.organizations.create(name="Integration Org", slug="integration-org")
        repos.organizations.add_member(organization_id=org.id, user_id=user.id, role="admin")
        session = repos.sessions.create("Integration Workspace", user.id, org.id)
        other_session = repos.sessions.create("Other Workspace", user.id, org.id)
        conn.commit()

        customers, orders = _ingest_csv_fixtures(
            tmp_path=tmp_path,
            settings=settings,
            repos=repos,
            object_store=object_store,
            user_id=user.id,
            organization_id=org.id,
            session_id=session.id,
        )
        llm = FakeLLMClient(customers_file_id=customers.id, orders_file_id=orders.id)
        asyncio.run(
            run_discovery_for_test(
                session_id=session.id,
                settings=settings,
                repos=repos,
                object_store=object_store,
                llm=llm,
                user_id=user.id,
            )
        )

        links = repos.links.list_for_session(session.id)
        assert any(
            link.file_a == orders.id
            and link.col_a == "Customer ID"
            and link.file_b == customers.id
            and link.col_b == "Customer ID"
            and link.direction == "many_to_one"
            for link in links
        )
        assert llm.link_verification_calls == 1

        orders_schema = repos.schemas.get(orders.id, 1)
        assert orders_schema is not None
        confidences = [column.confidence for column in orders_schema.columns]
        assert any(confidence < 0.85 for confidence in confidences)
        assert any(confidence != 1.0 for confidence in confidences)
        ship_date = next(column for column in orders_schema.columns if column.name == "Ship Date")
        assert ship_date.confidence_reason is not None
        assert "ambiguous_date_format" in ship_date.confidence_reason

        patch = asyncio.run(
            interpret_schema_correction(
                instruction="Describe amount clearly; the amount column is in lakhs.",
                schema_context=_schema_context_from_discovery(
                    _build_discovery_response(session.id, conn, user.id, org.id),
                    repos.data_docs.get(session.id),
                ),
                llm_client=llm,
                config=settings.processing.discovery,
            )
        )
        _apply_minor_schema_corrections(
            conn=conn,
            session_id=session.id,
            operations=minor_operations(patch),
            user_id=user.id,
        )
        conn.commit()
        minor_schema = repos.schemas.get(orders.id, 1)
        assert minor_schema is not None
        amount_minor = next(column for column in minor_schema.columns if column.name == "Amount")
        assert amount_minor.description == "Amount expressed in rupees after applying the scale."
        assert repos.artifacts.latest_for_file(orders.id, "processed_parquet") is None

        discovery = _build_discovery_response(session.id, conn, user.id, org.id)
        structural = selected_structural_operations(patch, {"scale_amount"})
        assert structural
        approval = apply_operations_to_payload(
            _approval_payload_from_discovery(discovery),
            structural,
        )
        apply_approval(
            session_id=session.id,
            user_id=user.id,
            organization_id=org.id,
            payload=approval,
            settings=settings,
            files_repo=repos.files,
            schemas_repo=repos.schemas,
            links_repo=repos.links,
            sessions_repo=repos.sessions,
            events_repo=repos.events,
            artifacts_repo=repos.artifacts,
            tables_repo=repos.tables,
            object_store=object_store,
        )
        _refresh_docs_from_approval(
            session_id=session.id,
            payload=approval,
            files_repo=repos.files,
            data_docs_repo=repos.data_docs,
        )
        for operation in structural:
            repos.audit.log(
                session_id=session.id,
                kind="schema_correction_applied",
                details={
                    "user_id": user.id,
                    "classification": operation.classification,
                    "op_type": operation.op_type,
                    "target": operation.target,
                    "before": operation.before_value,
                    "after": operation.after_value,
                    "transform": operation.transform,
                },
            )
        conn.commit()

        processed = preview_rows(
            file_id=orders.id,
            limit=6,
            files_repo=repos.files,
            settings=settings,
            artifacts_repo=repos.artifacts,
            object_store=object_store,
        )
        amount_index = processed["columns"].index("Amount")
        assert processed["rows"][0][amount_index] == 150000.0
        assert processed["rows"][1][amount_index] == 200000.0
        audit_count = conn.execute(
            "SELECT COUNT(*) AS value FROM audit_events WHERE session_id = ? AND kind = ?",
            (session.id, "schema_correction_applied"),
        ).fetchone()
        assert audit_count is not None
        assert int(audit_count["value"]) >= 2

        ocr = FakeOCREngine()
        embedding = FakeEmbeddingProvider()
        document = asyncio.run(
            _ingest_and_index_document(
                tmp_path=tmp_path,
                settings=settings,
                repos=repos,
                object_store=object_store,
                ocr=ocr,
                embedding=embedding,
                user_id=user.id,
                organization_id=org.id,
                session_id=session.id,
                filename="Regulation.pdf",
            )
        )
        pages = repos.documents.list_pages(document.id)
        assert len(pages) == 1
        assert pages[0].source == "ocr_retry"
        assert pages[0].low_confidence is True
        assert pages[0].image_object_key is not None
        assert ocr.page_calls >= 2

        chunks = repos.chunks.list_for_document(document.id)
        assert chunks
        assert all(chunk.low_confidence for chunk in chunks)
        assert any(chunk.section_no == "12" for chunk in chunks)
        assert any(chunk.clause_no == "12.1" for chunk in chunks)

        other_document = asyncio.run(
            _ingest_and_index_document(
                tmp_path=tmp_path,
                settings=settings,
                repos=repos,
                object_store=object_store,
                ocr=FakeOCREngine(),
                embedding=embedding,
                user_id=user.id,
                organization_id=org.id,
                session_id=other_session.id,
                filename="Other-Regulation.pdf",
            )
        )
        hits = asyncio.run(
            retrieve_document_chunks(
                query="shipment value",
                session_id=session.id,
                organization_id=org.id,
                user_id=user.id,
                chunk_store=repos.chunks,
                embedding_provider=embedding,
                settings=settings,
                top_n=5,
            )
        )
        assert hits
        assert all(hit.document_id == document.id for hit in hits)
        assert all(hit.document_id != other_document.id for hit in hits)
        assert any("page 1" in hit.citation and "section 12" in hit.citation for hit in hits)
        assert any(hit.low_confidence for hit in hits)

        registry = build_tool_registry(
            ToolContext(
                session_id=session.id,
                tables={},
                organization_id=org.id,
                user_id=user.id,
                settings=settings,
                regulation_search_store=repos.chunks,
                embedding_provider=embedding,
            )
        )
        assert "search_regulations" in registry.names()
        assert "interpret_schema_correction" not in registry.names()
        assert "apply_schema_correction" not in registry.names()
        tool_result = asyncio.run(
            registry.get("search_regulations").handler(
                {"query": "shipment value", "top_n": 5}
            )
        )
        assert tool_result["ok"] is True
        assert tool_result["result_count"] >= 1
        assert tool_result["has_low_confidence"] is True
        assert all(
            chunk["citation"]["document_id"] == document.id
            for chunk in tool_result["chunks"]
        )
    finally:
        conn.close()


async def run_discovery_for_test(
    *,
    session_id: str,
    settings: Settings,
    repos: Repos,
    object_store: ObjectStore,
    llm: FakeLLMClient,
    user_id: str,
) -> None:
    from cerno.services.discovery import run_discovery

    await run_discovery(
        session_id=session_id,
        settings=settings,
        files_repo=repos.files,
        sessions_repo=repos.sessions,
        links_repo=repos.links,
        data_docs_repo=repos.data_docs,
        events_repo=repos.events,
        llm_client=llm,
        schemas_repo=repos.schemas,
        artifacts_repo=repos.artifacts,
        object_store=object_store,
        user_id=user_id,
    )


@dataclass(frozen=True)
class Repos:
    users: UserRepository
    organizations: OrganizationRepository
    sessions: SessionRepository
    files: FileRepository
    schemas: SchemaRepository
    links: LinkRepository
    data_docs: DataDocRepository
    events: ProcessingEventRepository
    source_assets: SourceAssetRepository
    workspace_assets: WorkspaceAssetRepository
    artifacts: AssetArtifactRepository
    tables: WorkspaceTableRepository
    documents: DocumentRepository
    chunks: DocumentChunkRepository
    audit: AuditRepository


def _repos(conn: DbConnection) -> Repos:
    return Repos(
        users=UserRepository(conn),
        organizations=OrganizationRepository(conn),
        sessions=SessionRepository(conn),
        files=FileRepository(conn),
        schemas=SchemaRepository(conn),
        links=LinkRepository(conn),
        data_docs=DataDocRepository(conn),
        events=ProcessingEventRepository(conn),
        source_assets=SourceAssetRepository(conn),
        workspace_assets=WorkspaceAssetRepository(conn),
        artifacts=AssetArtifactRepository(conn),
        tables=WorkspaceTableRepository(conn),
        documents=DocumentRepository(conn),
        chunks=DocumentChunkRepository(conn),
        audit=AuditRepository(conn),
    )


def _settings(tmp_path: Path, postgres_url: str) -> Settings:
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "config.toml").write_text(
        """
[processing.embedding]
provider = "fake"
model = "fake-embedding"
dimension = 3
batch_size = 2
chunk_max_chars = 900
chunk_min_chars = 1

[processing.ocr]
engine = "fake"
pdf_quality_threshold = 0.72
pdf_max_pages_per_document = 5
text_layer_min_chars = 10
""".strip(),
        encoding="utf-8",
    )
    return Settings(
        _env_file=None,
        data_root=data_root,
        postgres_url=postgres_url,
        llm_api_key="test-key",
        schema_confidence_threshold=0.85,
        link_min_shared_distinct=5,
        link_min_distinct=3,
        link_low_cardinality_distinct=12,
    )


def _ingest_csv_fixtures(
    *,
    tmp_path: Path,
    settings: Settings,
    repos: Repos,
    object_store: ObjectStore,
    user_id: str,
    organization_id: str,
    session_id: str,
) -> tuple[Any, Any]:
    customers_path = tmp_path / "customers.csv"
    customers_path.write_text(
        "\n".join(
            [
                "customer_id,name",
                "C001,Asha",
                "C002,Ben",
                "C003,Chitra",
                "C004,Dev",
                "C005,Esha",
                "C006,Farah",
            ]
        ),
        encoding="utf-8",
    )
    orders_path = tmp_path / "orders.csv"
    orders_path.write_text(
        "\n".join(
            [
                "order_id,customer_id,amount_lakh,ship_date",
                "O001,C001,1.5,01/02/2024",
                "O002,C002,2.0,03/04/2024",
                "O003,C003,3.25,05/06/2024",
                "O004,C004,4.0,07/08/2024",
                "O005,C005,5.5,09/10/2024",
                "O006,C001,6.0,11/12/2024",
            ]
        ),
        encoding="utf-8",
    )
    customers = ingest_file(
        source_path=customers_path,
        original_filename="customers.csv",
        original_content_type="text/csv",
        original_size_bytes=customers_path.stat().st_size,
        user_id=user_id,
        organization_id=organization_id,
        session_id=session_id,
        settings=settings,
        files_repo=repos.files,
        source_assets_repo=repos.source_assets,
        workspace_assets_repo=repos.workspace_assets,
        artifacts_repo=repos.artifacts,
        tables_repo=repos.tables,
        object_store=object_store,
    )[0].file
    orders = ingest_file(
        source_path=orders_path,
        original_filename="orders.csv",
        original_content_type="text/csv",
        original_size_bytes=orders_path.stat().st_size,
        user_id=user_id,
        organization_id=organization_id,
        session_id=session_id,
        settings=settings,
        files_repo=repos.files,
        source_assets_repo=repos.source_assets,
        workspace_assets_repo=repos.workspace_assets,
        artifacts_repo=repos.artifacts,
        tables_repo=repos.tables,
        object_store=object_store,
    )[0].file
    return customers, orders


async def _ingest_and_index_document(
    *,
    tmp_path: Path,
    settings: Settings,
    repos: Repos,
    object_store: ObjectStore,
    ocr: FakeOCREngine,
    embedding: FakeEmbeddingProvider,
    user_id: str,
    organization_id: str,
    session_id: str,
    filename: str,
) -> Any:
    pdf_path = tmp_path / filename
    _write_blank_pdf(pdf_path)
    result = await ingest_document(
        source_path=pdf_path,
        original_filename=filename,
        original_content_type="application/pdf",
        original_size_bytes=pdf_path.stat().st_size,
        user_id=user_id,
        organization_id=organization_id,
        session_id=session_id,
        settings=settings,
        documents_repo=repos.documents,
        source_assets_repo=repos.source_assets,
        workspace_assets_repo=repos.workspace_assets,
        artifacts_repo=repos.artifacts,
        object_store=object_store,
        ocr_engine=ocr,
    )
    await index_document_chunks(
        document=result.document,
        pages=result.pages,
        chunk_repo=repos.chunks,
        embedding_provider=embedding,
        settings=settings,
    )
    return result.document


def _write_blank_pdf(path: Path) -> None:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(612, 792)
    try:
        pdf.save(path)
    finally:
        page.close()
        pdf.close()


class FakeLLMClient:
    def __init__(self, *, customers_file_id: str, orders_file_id: str) -> None:
        self.customers_file_id = customers_file_id
        self.orders_file_id = orders_file_id
        self.link_verification_calls = 0

    async def respond(self, **kwargs: Any) -> LLMResponse:
        response_format = kwargs.get("response_format") or {}
        name = response_format.get("name")
        if name == "discovery":
            return self._json_response(self._discovery_payload())
        if name == "discovery_recheck":
            return self._json_response({"corrections": []})
        if name == "link_verification":
            self.link_verification_calls += 1
            return self._json_response(self._link_verification_payload(kwargs["input"]))
        if name == "schema_correction_patch":
            return self._json_response(self._schema_correction_payload())
        return LLMResponse(content="Profiles indicate customer orders linked to customers.")

    def _json_response(self, payload: dict[str, Any]) -> LLMResponse:
        return LLMResponse(content=json.dumps(payload), raw={"output": []}, status="completed")

    def _discovery_payload(self) -> dict[str, Any]:
        return {
            "files": [
                {
                    "file_id": self.customers_file_id,
                    "friendly_name": "Customers",
                    "description": "One row per customer.",
                    "header_row": 0,
                    "columns": [
                        {
                            "column_id": "customer_id",
                            "name": "Customer ID",
                            "description": "Stable customer identifier.",
                            "dtype": "string",
                        },
                        {
                            "column_id": "name",
                            "name": "Customer Name",
                            "description": "Customer display name.",
                            "dtype": "string",
                        },
                    ],
                },
                {
                    "file_id": self.orders_file_id,
                    "friendly_name": "Orders",
                    "description": "One row per order.",
                    "header_row": 0,
                    "columns": [
                        {
                            "column_id": "order_id",
                            "name": "Order ID",
                            "description": "Stable order identifier.",
                            "dtype": "string",
                        },
                        {
                            "column_id": "customer_id",
                            "name": "Customer ID",
                            "description": "Customer placing the order.",
                            "dtype": "string",
                        },
                        {
                            "column_id": "amount_lakh",
                            "name": "Amount",
                            "description": "Amount supplied by the uploaded file.",
                            "dtype": "float",
                        },
                        {
                            "column_id": "ship_date",
                            "name": "Ship Date",
                            "description": "Date associated with shipment.",
                            "dtype": "date",
                        },
                    ],
                },
            ],
            "links": [],
            "overview": "Orders can be analyzed with customer details.",
            "documentation": {
                "overview": "Orders can be analyzed with customer details.",
                "files": [
                    {
                        "file_id": self.customers_file_id,
                        "name": "Customers",
                        "description": "Customer master data.",
                        "grain": "One row per customer.",
                        "row_count": 7,
                        "columns": [
                            {"name": "Customer ID", "meaning": "Stable customer identifier."},
                            {"name": "Customer Name", "meaning": "Customer display name."},
                        ],
                        "key_columns": ["Customer ID"],
                        "date_columns": [],
                        "measure_columns": [],
                        "category_columns": [],
                        "caveats": [],
                    },
                    {
                        "file_id": self.orders_file_id,
                        "name": "Orders",
                        "description": "Order transactions.",
                        "grain": "One row per order.",
                        "row_count": 7,
                        "columns": [
                            {"name": "Order ID", "meaning": "Stable order identifier."},
                            {"name": "Customer ID", "meaning": "Customer placing the order."},
                            {"name": "Amount", "meaning": "Order amount."},
                            {"name": "Ship Date", "meaning": "Shipment date."},
                        ],
                        "key_columns": ["Order ID", "Customer ID"],
                        "date_columns": ["Ship Date"],
                        "measure_columns": ["Amount"],
                        "category_columns": [],
                        "caveats": ["Ship Date is ambiguous and needs review."],
                    },
                ],
                "relationships": [],
                "glossary": [{"term": "Customer ID", "meaning": "Shared customer key."}],
                "usage_notes": [],
                "starter_questions": ["What is total order amount by customer?"],
            },
        }

    def _link_verification_payload(self, input_payload: Any) -> dict[str, Any]:
        content = input_payload[0]["content"]
        candidates = json.loads(str(content).split("\n", 1)[1])
        return {
            "verifications": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "confirmed": True,
                    "direction": candidate.get("direction") or "many_to_one",
                    "summary": "Customer ID connects Orders to Customers.",
                }
                for candidate in candidates
            ]
        }

    def _schema_correction_payload(self) -> dict[str, Any]:
        return {
            "operations": [
                {
                    "op_id": "describe_amount",
                    "target_type": "column",
                    "target": {
                        "file_id": self.orders_file_id,
                        "column_id": "amount_lakh",
                    },
                    "op_type": "set_column_description",
                    "before_value": "Amount supplied by the uploaded file.",
                    "after_value": "Amount expressed in rupees after applying the scale.",
                    "description": "Clarify the amount column description.",
                    "classification": "minor",
                    "transform": None,
                },
                {
                    "op_id": "scale_amount",
                    "target_type": "column",
                    "target": {
                        "file_id": self.orders_file_id,
                        "column_id": "amount_lakh",
                    },
                    "op_type": "scale_column",
                    "before_value": "lakhs",
                    "after_value": "rupees",
                    "description": "Amounts are supplied in lakhs and should be stored in rupees.",
                    "classification": "structural",
                    "transform": {"kind": "scale", "factor": 100000.0},
                },
            ]
        }


class FakeOCREngine:
    name = "fake"
    model = "fake-ocr"

    def __init__(self) -> None:
        self.page_calls = 0

    async def extract_text(
        self,
        *,
        content: bytes,
        mime_type: str | None = None,
        filename: str | None = None,
    ) -> str:
        return self._markdown()

    async def extract_page_image(
        self,
        *,
        image: bytes,
        mime_type: str = "image/png",
        filename: str | None = None,
        llm_client: object | None = None,
    ) -> OCRPageResult:
        self.page_calls += 1
        return OCRPageResult(
            markdown=self._markdown(),
            self_confidence=0.05,
            illegible_regions=("lower right table",),
            has_tables=False,
            layout_notes="low-confidence fake OCR page",
        )

    def _markdown(self) -> str:
        return """
# Regulation Guide

Section 12 Shipment Value

12.1 [illegible] [illegible] xqzv qwrty zzz999 shipment value shall be verified against the invoice amount.
""".strip()


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
        return [_vector_for_text(text) for text in texts]


def _vector_for_text(text: str) -> list[float]:
    lower = text.lower()
    return [
        1.0 if "shipment" in lower else 0.1,
        1.0 if "value" in lower else 0.1,
        float((len(text) % 7) + 1) / 10.0,
    ]


class LocalObjectStore(ObjectStore):
    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_path(
        self, source: Path, object_key: str, *, content_type: str | None = None
    ) -> StoredObject:
        destination = self._path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return StoredObject(
            backend=self.backend,
            object_key=object_key,
            size_bytes=destination.stat().st_size,
            content_type=content_type,
        )

    def get_to_path(self, object_key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(object_key), destination)
        return destination

    def delete(self, object_key: str) -> None:
        self._path(object_key).unlink(missing_ok=True)

    def list_keys(self, prefix: str) -> list[str]:
        return [
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and path.relative_to(self.root).as_posix().startswith(prefix)
        ]

    def head(self, object_key: str) -> StoredObject:
        path = self._path(object_key)
        return StoredObject(
            backend=self.backend,
            object_key=object_key,
            size_bytes=path.stat().st_size,
        )

    def presigned_put_url(
        self, object_key: str, *, content_type: str, expires_seconds: int
    ) -> str:
        return f"local://put/{object_key}"

    def presigned_get_url(self, object_key: str, *, expires_seconds: int) -> str:
        return f"local://get/{object_key}"

    def _path(self, object_key: str) -> Path:
        return self.root / object_key
