from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from cerno.config import DiscoveryProcessingConfig, Settings
from cerno.llm import LLMResponse
from cerno.models import File
from cerno.services.discovery import (
    DiscoveredColumn,
    DiscoveredFile,
    DiscoveryResult,
    _targeted_reask_low_confidence_fields,
    _validate_discovery_columns,
)
from cerno.services.discovery_validation import FieldValidationInput, validate_discovered_column


def test_confidently_correct_column_gets_high_confidence() -> None:
    validation = validate_discovered_column(
        FieldValidationInput(
            file_id="file-1",
            file_name="Orders",
            column_id="amount",
            name="Amount",
            dtype="float",
            description="Order amount.",
            values=[10.5, 20.0, 30.25],
        )
    )

    assert validation.confidence >= 0.95
    assert validation.reasons == ()


def test_type_contradiction_gets_low_confidence_and_reason() -> None:
    validation = validate_discovered_column(
        FieldValidationInput(
            file_id="file-1",
            file_name="Orders",
            column_id="amount",
            name="Amount",
            dtype="date",
            description="Order amount.",
            values=["abc", "def", "ghi"],
        )
    )

    assert validation.confidence < 0.85
    assert "type_parse_rate_low" in validation.reasons


def test_ambiguous_date_stays_flagged() -> None:
    validation = validate_discovered_column(
        FieldValidationInput(
            file_id="file-1",
            file_name="Orders",
            column_id="date",
            name="Date",
            dtype="date",
            description="Ambiguous date.",
            values=["01/02/2024", "03/04/2024"],
        )
    )

    assert validation.confidence < 0.85
    assert "ambiguous_date_format" in validation.reasons


def test_targeted_reask_only_fires_for_low_confidence_fields_and_is_capped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raw_path = Path(tmp) / "raw.parquet"
        pl.DataFrame(
            {
                "c0": ["amount", "oops", "nope"],
                "c1": ["maybe_date", "01/02/2024", "03/04/2024"],
                "c2": ["rate", "bad", "worse"],
                "c3": ["name", "alpha", "beta"],
            }
        ).write_parquet(raw_path)
        file = File(
            id="file-1",
            session_id="session-1",
            filename="sample.csv",
            parquet_path=str(raw_path),
            raw_parquet_path=str(raw_path),
            row_count=3,
            created_at=datetime.now(UTC),
        )
        result = DiscoveryResult(
            files=[
                DiscoveredFile(
                    file_id="file-1",
                    friendly_name="Sample",
                    description="Sample file.",
                    header_row=0,
                    columns=[
                        DiscoveredColumn("amount", "Amount", "Amount.", "float"),
                        DiscoveredColumn("maybe_date", "Maybe Date", "Date.", "date"),
                        DiscoveredColumn("rate", "Rate", "Rate.", "float"),
                        DiscoveredColumn("name", "Name", "Name.", "string"),
                    ],
                )
            ],
            links=[],
            overview="Sample.",
        )
        validations = _validate_discovery_columns(files=[file], discovered_files=result.files)
        client = FakeReaskClient()

        asyncio.run(
            _targeted_reask_low_confidence_fields(
                llm_client=client,  # type: ignore[arg-type]
                result=result,
                files=[file],
                validations=validations,
                settings=Settings(
                    _env_file=None,
                    schema_confidence_threshold=0.85,
                    discovery_reask_max_fields=2,
                ),
                config=DiscoveryProcessingConfig(),
            )
        )

    assert client.calls == 1
    assert client.field_count == 2


class FakeReaskClient:
    def __init__(self) -> None:
        self.calls = 0
        self.field_count = 0

    async def respond(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        content = kwargs["input"][0]["content"]
        fields = json.loads(str(content).split("\n", 1)[1])
        self.field_count = len(fields)
        corrections = [
            {
                "file_id": field["file_id"],
                "column_id": field["column_id"],
                "name": field["current"]["name"],
                "description": field["current"]["description"],
                "dtype": "string",
            }
            for field in fields
        ]
        return LLMResponse(
            content=json.dumps({"corrections": corrections}),
            raw={"output": []},
            status="completed",
        )
