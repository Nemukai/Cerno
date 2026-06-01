from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
import polars as pl

from cerno.config import DiscoveryProcessingConfig, Settings
from cerno.llm import LLMClient
from cerno.models import (
    DataDoc,
    DataDocColumn,
    DataDocFile,
    DataDocGlossaryItem,
    DataDocRelationship,
    File,
    FileSchema,
    InferredKind,
    LinkDirection,
    SchemaColumn,
)
from cerno.repositories import (
    AssetArtifactRepository,
    DataDocRepository,
    FileRepository,
    LinkRepository,
    ProcessingEventRepository,
    SchemaRepository,
    SessionRepository,
)
from cerno.services.artifact_cache import ensure_file_artifact_cached
from cerno.services.discovery_validation import (
    ColumnValidation,
    FieldValidationInput,
    validate_discovered_column,
)
from cerno.services.ingest import first_n_raw_rows, slugify_table_name
from cerno.services.profiling import profile_table
from cerno.services.relationships import (
    RelationshipCandidate,
    RelationshipColumn,
    RelationshipScoringConfig,
    build_relationship_column,
    score_relationship_candidates,
)
from cerno.storage import ObjectStore

LAYOUT_SAMPLE_ROWS = 3
LINK_VALUE_SAMPLE_LIMIT = 5_000
logger = logging.getLogger(__name__)

SIMPLE_DTYPE_TO_INFERRED_KIND: dict[str, InferredKind] = {
    "string": "string",
    "int": "int",
    "integer": "int",
    "float": "float",
    "number": "float",
    "numeric": "float",
    "date": "date",
    "datetime": "datetime",
    "timestamp": "datetime",
    "bool": "bool",
    "boolean": "bool",
    "category": "category",
}


class DiscoveryError(RuntimeError):
    pass


@dataclass
class DiscoveredColumn:
    column_id: str
    name: str
    description: str
    dtype: str


@dataclass
class DiscoveredFile:
    file_id: str
    friendly_name: str
    description: str
    header_row: int
    columns: list[DiscoveredColumn]


@dataclass
class DiscoveredLink:
    file_a_id: str
    col_a: str
    file_b_id: str
    col_b: str
    direction: LinkDirection
    summary: str


@dataclass
class DiscoveryResult:
    files: list[DiscoveredFile]
    links: list[DiscoveredLink]
    overview: str
    documentation: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)


SYSTEM_PROMPT = """You are Cerno's highly capable data discovery engine. Tabular files have been uploaded for analysis.
Your job is to deeply analyze the column profiles, representative examples, and Python analysis notes, then systematically determine:

1. The exact header row (0-indexed). Account for files with title rows, blank rows, or metadata before actual headers.
2. A clean, human-friendly business name for each file (e.g., 'Customer Orders', 'Inventory Log'). Omit extensions and raw timestamps.
3. A concise, non-technical 1-2 sentence description of the file's primary purpose and grain (what one row represents).
4. The exact schema: column names (post-header), plain-language descriptions, and the correct data type (string, int, float, date, datetime, bool, category).

Do not propose cross-file links in the discovery JSON. Return an empty links array; a separate deterministic candidate scorer and verifier will handle relationships.

Finally, construct the internal DataDoc (documentation):
- Write a 2-4 sentence cohesive overview of the entire workspace and how the files interconnect.
- Ensure the 'grain' and 'caveats' for each file are well-documented, but do not invent relationship documentation.
- Generate a robust glossary of business terms or acronyms found in the headers or data.
- Supply 3-5 highly relevant, analytical 'starter questions' that a user might want to ask this data.

Ground all decisions in the data. Return strictly formatted JSON matching the schema. Do not include markdown fences or conversational text."""


RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["files", "links", "overview", "documentation"],
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "file_id",
                    "friendly_name",
                    "description",
                    "header_row",
                    "columns",
                ],
                "properties": {
                    "file_id": {"type": "string"},
                    "friendly_name": {"type": "string"},
                    "description": {"type": "string"},
                    "header_row": {"type": "integer", "minimum": 0},
                    "columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["column_id", "name", "description", "dtype"],
                            "properties": {
                                "column_id": {"type": "string"},
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "dtype": {
                                    "type": "string",
                                    "enum": [
                                        "string",
                                        "int",
                                        "float",
                                        "date",
                                        "datetime",
                                        "bool",
                                        "category",
                                    ],
                                },
                            },
                        },
                    },
                },
            },
        },
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "file_a_id",
                    "col_a",
                    "file_b_id",
                    "col_b",
                    "direction",
                    "summary",
                ],
                "properties": {
                    "file_a_id": {"type": "string"},
                    "col_a": {"type": "string"},
                    "file_b_id": {"type": "string"},
                    "col_b": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": ["one_to_one", "many_to_one", "many_to_many"],
                    },
                    "summary": {"type": "string"},
                },
            },
        },
        "overview": {"type": "string"},
        "documentation": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "overview",
                "files",
                "relationships",
                "glossary",
                "usage_notes",
                "starter_questions",
            ],
            "properties": {
                "overview": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "file_id",
                            "name",
                            "description",
                            "grain",
                            "row_count",
                            "columns",
                            "key_columns",
                            "date_columns",
                            "measure_columns",
                            "category_columns",
                            "caveats",
                        ],
                        "properties": {
                            "file_id": {"type": "string"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "grain": {"type": "string"},
                            "row_count": {"type": "integer"},
                            "columns": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["name", "meaning"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "meaning": {"type": "string"}
                                    },
                                },
                            },
                            "key_columns": {"type": "array", "items": {"type": "string"}},
                            "date_columns": {"type": "array", "items": {"type": "string"}},
                            "measure_columns": {"type": "array", "items": {"type": "string"}},
                            "category_columns": {"type": "array", "items": {"type": "string"}},
                            "caveats": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "relationships": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "left_file_id",
                            "left_column",
                            "right_file_id",
                            "right_column",
                            "explanation",
                        ],
                        "properties": {
                            "left_file_id": {"type": "string"},
                            "left_column": {"type": "string"},
                            "right_file_id": {"type": "string"},
                            "right_column": {"type": "string"},
                            "explanation": {"type": "string"},
                        },
                    },
                },
                "glossary": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["term", "meaning"],
                        "properties": {
                            "term": {"type": "string"},
                            "meaning": {"type": "string"},
                        },
                    },
                },
                "usage_notes": {"type": "array", "items": {"type": "string"}},
                "starter_questions": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}


LINK_VERIFIER_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verifications"],
    "properties": {
        "verifications": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate_id", "confirmed", "direction", "summary"],
                "properties": {
                    "candidate_id": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                    "direction": {
                        "type": "string",
                        "enum": ["one_to_one", "many_to_one", "many_to_many"],
                    },
                    "summary": {"type": "string"},
                },
            },
        },
    },
}


REASK_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["corrections"],
    "properties": {
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file_id", "column_id", "name", "description", "dtype"],
                "properties": {
                    "file_id": {"type": "string"},
                    "column_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "dtype": {
                        "type": "string",
                        "enum": [
                            "string",
                            "int",
                            "float",
                            "date",
                            "datetime",
                            "bool",
                            "category",
                        ],
                    },
                },
            },
        },
    },
}


LINK_VERIFIER_PROMPT = """You verify deterministic relationship candidates for Cerno.
Confirm a candidate only when the evidence supports a real analytical relationship, such as a foreign key or intentional shared business key.
Reject coincidental overlaps, low-cardinality domains, calendar/date coincidences, booleans, and columns whose names or types suggest different meanings.
Do not add candidates. Return one verification object for every candidate_id supplied."""


REASK_PROMPT = """You are Cerno's targeted schema reviewer.
You will receive only low-confidence fields from an earlier strict schema pass, plus validator evidence and compact column profiles.
Correct only the supplied fields. Prefer conservative, general-purpose types. If evidence is ambiguous, keep the best field name/description but choose the type that validates best against the observed values.
Return strictly the requested corrections array. Do not add files or columns."""


def _parse_json_block(content: str) -> dict[str, Any]:
    stripped = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        return cast(dict[str, Any], json.loads(stripped))
    except json.JSONDecodeError as exc:
        raise DiscoveryError(
            f"LLM returned invalid JSON: {exc}\n--- raw ---\n{content[:2000]}"
        ) from exc


async def _respond_json_schema_with_retry(
    *,
    llm_client: LLMClient,
    input_payload: list[dict[str, Any]],
    instructions: str,
    model: str,
    reasoning_effort: str,
    reasoning_summary: str,
    response_format: dict[str, Any],
    attempts: int = 2,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        response = await llm_client.respond(
            input=input_payload,
            instructions=instructions,
            model=model,
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
            response_format=response_format,
        )
        try:
            _raise_for_refusal(response.raw, response.status)
            return _parse_json_block(response.content)
        except DiscoveryError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            await asyncio.sleep(0.5 * (2**attempt))
    assert last_error is not None
    raise last_error


def _raise_for_refusal(raw: dict[str, Any], status: str) -> None:
    if status != "completed":
        raise DiscoveryError(f"LLM response did not complete: status={status}")
    for item in raw.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []) or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal" or part.get("refusal"):
                raise DiscoveryError("LLM refused the structured discovery request")


def _parse_response(payload: dict[str, Any]) -> DiscoveryResult:
    files_raw = payload.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise DiscoveryError("LLM response missing 'files' array")
    links_raw = payload.get("links") or []
    overview = str(payload.get("overview") or "").strip()

    files: list[DiscoveredFile] = []
    for entry in files_raw:
        cols_raw = entry.get("columns") or []
        cols = [
            DiscoveredColumn(
                column_id=str(c.get("column_id") or ""),
                name=str(c.get("name") or ""),
                description=str(c.get("description") or ""),
                dtype=str(c.get("dtype") or "string"),
            )
            for c in cols_raw
        ]
        files.append(
            DiscoveredFile(
                file_id=str(entry.get("file_id") or ""),
                friendly_name=str(entry.get("friendly_name") or ""),
                description=str(entry.get("description") or ""),
                header_row=int(entry.get("header_row") or 0),
                columns=cols,
            )
        )
    links = [
        DiscoveredLink(
            file_a_id=str(entry.get("file_a_id") or ""),
            col_a=str(entry.get("col_a") or ""),
            file_b_id=str(entry.get("file_b_id") or ""),
            col_b=str(entry.get("col_b") or ""),
            direction=cast(LinkDirection, entry.get("direction") or "many_to_many"),
            summary=str(entry.get("summary") or ""),
        )
        for entry in links_raw
    ]
    documentation = payload.get("documentation")
    if not isinstance(documentation, dict):
        documentation = {}
    return DiscoveryResult(
        files=files,
        links=links,
        overview=overview,
        documentation=documentation,
        raw_response=payload,
    )


def _build_prompt(file_payload: list[dict[str, Any]], analysis: str) -> str:
    return (
        "For each file below, decide the header row, a 2-4 word business-friendly name, "
        "a concise non-technical description, and the columns (post-header) with type "
        "and plain-language description. Then write a session overview and create the "
        "internal documentation used by the chat analyst.\n\n"
        "Use the column profiles as the primary evidence. Use example rows only for "
        "layout/header context, and use Python analysis notes as higher-confidence "
        "evidence than examples when they conflict.\n\n"
        "Do not propose cross-file links. Set top-level links to [] and "
        "documentation.relationships to []; link verification runs separately over "
        "deterministic candidates.\n\n"
        "Return STRICT JSON matching the provided schema. No prose, no fences.\n\n"
        f"Python analysis notes:\n{analysis}\n\n"
        f"Files:\n{json.dumps(file_payload, default=str, indent=2)}"
    )


ANALYSIS_SUMMARY_PROMPT = """You are Cerno's internal data profiler. Below are pre-computed column profiles
for every uploaded file. Synthesise them into concise analysis notes covering:

- File Purpose & Grain: What does each file represent? What does one row mean?
- Schema Reality: Likely header row index (0 = first row is already headers). Are there title/metadata rows to skip?
- Column Roles: Primary keys/IDs, date columns, key measures, categoricals, and highly null columns.
- Data Quality & Caveats: Missing data patterns or gotchas a non-technical user must know.

Do not propose cross-file relationships. Those are verified separately from deterministic candidates.
Be concise. Focus on facts that help build an accurate schema."""


def _table_name(filename: str, seen: set[str]) -> str:
    base = slugify_table_name(filename)
    name = base
    idx = 2
    while name in seen:
        name = f"{base}_{idx}"
        idx += 1
    seen.add(name)
    return name


def _load_raw_tables(files: list[File]) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    tables: dict[str, pd.DataFrame] = {}
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for file in files:
        if not file.raw_parquet_path:
            raise DiscoveryError(f"file {file.filename} missing raw parquet")
        table_name = _table_name(file.filename, seen)
        frame = pd.read_parquet(file.raw_parquet_path)
        tables[table_name] = frame
        catalog.append(
            {
                "file_id": file.id,
                "filename": file.filename,
                "table_name": table_name,
                "row_count": file.row_count,
                "raw_columns": list(map(str, frame.columns)),
            }
        )
    return tables, catalog


def _profile_all_tables(
    raw_tables: dict[str, pd.DataFrame],
) -> dict[str, dict[str, Any]]:
    return {name: profile_table(df) for name, df in raw_tables.items()}


async def _summarize_analysis(
    *,
    llm_client: LLMClient,
    profiles: dict[str, dict[str, Any]],
    catalog: list[dict[str, Any]],
    config: DiscoveryProcessingConfig,
) -> str:
    user_content = (
        f"{ANALYSIS_SUMMARY_PROMPT}\n\n"
        f"Table catalog:\n{json.dumps(catalog, default=str, indent=2)}\n\n"
        f"Column profiles:\n{json.dumps(profiles, default=str, indent=2)}"
    )
    response = await llm_client.respond(
        input=[{"role": "user", "content": user_content}],
        instructions=(
            "You are Cerno's internal data profiler. Write concise analysis notes "
            "about grain and column roles based on the pre-computed profiles. "
            "Do not propose relationships, ask for tools, or request additional data."
        ),
        model=config.analysis_model,
        reasoning_effort=config.analysis_reasoning_effort,
        reasoning_summary=config.reasoning_summary,
    )
    return response.content


def _data_doc_from_result(
    session_id: str,
    result: DiscoveryResult,
    *,
    validations: dict[tuple[str, str], ColumnValidation] | None = None,
    link_candidates: dict[frozenset[tuple[str, str]], RelationshipCandidate] | None = None,
) -> DataDoc:
    now = datetime.now(UTC)
    raw = result.documentation
    discovered_by_id = {file.file_id: file for file in result.files}
    file_docs = [
        DataDocFile(
            file_id=str(item.get("file_id") or ""),
            name=str(item.get("name") or ""),
            description=str(item.get("description") or ""),
            grain=str(item.get("grain") or ""),
            row_count=int(item.get("row_count") or 0),
            columns=[
                _data_doc_column(
                    file_id=str(item.get("file_id") or ""),
                    raw_column=col,
                    discovered=discovered_by_id.get(str(item.get("file_id") or "")),
                    validations=validations,
                )
                for col in item.get("columns", [])
                if isinstance(col, dict)
            ],
            key_columns=[str(v) for v in item.get("key_columns", [])],
            date_columns=[str(v) for v in item.get("date_columns", [])],
            measure_columns=[str(v) for v in item.get("measure_columns", [])],
            category_columns=[str(v) for v in item.get("category_columns", [])],
            caveats=[str(v) for v in item.get("caveats", [])],
        )
        for item in raw.get("files", [])
        if isinstance(item, dict)
    ]
    relationships = [
        _data_doc_relationship(
            raw_relationship=item,
            link_candidates=link_candidates,
        )
        for item in raw.get("relationships", [])
        if isinstance(item, dict)
    ]
    if not relationships:
        relationships = [
            _data_doc_relationship(
                raw_relationship={
                    "left_file_id": link.file_a_id,
                    "left_column": link.col_a,
                    "right_file_id": link.file_b_id,
                    "right_column": link.col_b,
                    "explanation": link.summary,
                },
                link_candidates=link_candidates,
            )
            for link in result.links
        ]
    glossary = [
        DataDocGlossaryItem(
            term=str(item.get("term") or ""),
            meaning=str(item.get("meaning") or ""),
        )
        for item in raw.get("glossary", [])
        if isinstance(item, dict)
    ]
    return DataDoc(
        session_id=session_id,
        overview=str(raw.get("overview") or result.overview),
        files=file_docs,
        relationships=relationships,
        glossary=glossary,
        usage_notes=[str(v) for v in raw.get("usage_notes", [])],
        starter_questions=[str(v) for v in raw.get("starter_questions", [])],
        created_at=now,
        updated_at=now,
    )


def _data_doc_column(
    *,
    file_id: str,
    raw_column: dict[str, Any],
    discovered: DiscoveredFile | None,
    validations: dict[tuple[str, str], ColumnValidation] | None,
) -> DataDocColumn:
    name = str(raw_column.get("name") or "")
    matched = None
    if discovered is not None:
        matched = next((column for column in discovered.columns if column.name == name), None)
    validation = (
        _column_validation(validations, file_id, matched)
        if matched is not None
        else None
    )
    return DataDocColumn(
        name=name,
        dtype=matched.dtype if matched is not None else "auto",
        meaning=str(raw_column.get("meaning") or ""),
        role="auto",
        confidence=validation.confidence if validation is not None else 1.0,
        low_confidence_reasons=list(validation.reasons) if validation is not None else [],
    )


def _data_doc_relationship(
    *,
    raw_relationship: dict[str, Any],
    link_candidates: dict[frozenset[tuple[str, str]], RelationshipCandidate] | None,
) -> DataDocRelationship:
    relationship = DataDocRelationship(
        left_file_id=str(raw_relationship.get("left_file_id") or ""),
        left_column=str(raw_relationship.get("left_column") or ""),
        right_file_id=str(raw_relationship.get("right_file_id") or ""),
        right_column=str(raw_relationship.get("right_column") or ""),
        explanation=str(raw_relationship.get("explanation") or ""),
    )
    if link_candidates is None:
        return relationship
    key = frozenset(
        {
            (relationship.left_file_id, relationship.left_column.lower()),
            (relationship.right_file_id, relationship.right_column.lower()),
        }
    )
    candidate = link_candidates.get(key)
    if candidate is None:
        return relationship
    relationship.confidence = candidate.score
    relationship.low_confidence_reasons = (
        [] if candidate.score >= 0.85 else ["link_confidence_below_threshold"]
    )
    return relationship


def _link_key(link: DiscoveredLink | RelationshipCandidate) -> frozenset[tuple[str, str]]:
    if isinstance(link, RelationshipCandidate):
        return frozenset(
            {
                (link.child_file_id, link.child_column.lower()),
                (link.parent_file_id, link.parent_column.lower()),
            }
        )
    return frozenset(
        {
            (link.file_a_id, link.col_a.lower()),
            (link.file_b_id, link.col_b.lower()),
        }
    )


def _relationship_scoring_config(settings: Settings) -> RelationshipScoringConfig:
    return RelationshipScoringConfig(
        containment_threshold=settings.link_containment_threshold,
        parent_uniqueness_threshold=settings.link_parent_uniqueness_threshold,
        score_threshold=settings.link_candidate_score_threshold,
        min_distinct=settings.link_min_distinct,
        min_shared_distinct=settings.link_min_shared_distinct,
        low_cardinality_distinct=settings.link_low_cardinality_distinct,
        max_avg_length=settings.link_max_avg_length,
        max_candidates=settings.link_max_llm_candidates,
    )


def _column_key(file_id: str, column: DiscoveredColumn) -> tuple[str, str]:
    return (file_id, (column.column_id or column.name).strip().lower())


def _validation_values(file: File, discovered: DiscoveredFile, col_idx: int) -> list[Any]:
    if not file.raw_parquet_path or col_idx >= len(discovered.columns):
        return []
    try:
        rows = pl.read_parquet(file.raw_parquet_path).to_numpy().tolist()
    except Exception:
        logger.exception("event=discovery.validation.read_failed file_id=%s", file.id)
        return []
    if discovered.header_row >= len(rows):
        return []
    data_rows = rows[discovered.header_row + 1 :]
    values: list[Any] = []
    for row in data_rows:
        values.append(row[col_idx] if col_idx < len(row) else None)
    return values


def _validate_discovery_columns(
    *, files: list[File], discovered_files: list[DiscoveredFile]
) -> dict[tuple[str, str], ColumnValidation]:
    files_by_id = {file.id: file for file in files}
    validations: dict[tuple[str, str], ColumnValidation] = {}
    for discovered in discovered_files:
        file = files_by_id.get(discovered.file_id)
        sibling_names = tuple(column.name for column in discovered.columns)
        for idx, column in enumerate(discovered.columns):
            if file is None:
                validations[_column_key(discovered.file_id, column)] = ColumnValidation(
                    confidence=0.0,
                    reasons=("file_not_found",),
                    profile={},
                )
                continue
            values = _validation_values(file, discovered, idx)
            validations[_column_key(discovered.file_id, column)] = validate_discovered_column(
                FieldValidationInput(
                    file_id=discovered.file_id,
                    file_name=discovered.friendly_name or file.filename,
                    column_id=column.column_id or column.name,
                    name=column.name,
                    dtype=column.dtype,
                    description=column.description,
                    values=values,
                    sibling_names=sibling_names,
                )
            )
    return validations


def _low_confidence_reask_payload(
    *,
    result: DiscoveryResult,
    validations: dict[tuple[str, str], ColumnValidation],
    threshold: float,
    limit: int,
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for discovered in result.files:
        for column in discovered.columns:
            key = _column_key(discovered.file_id, column)
            validation = validations.get(key)
            if validation is None or validation.confidence >= threshold:
                continue
            fields.append(
                {
                    "file_id": discovered.file_id,
                    "file_name": discovered.friendly_name,
                    "column_id": column.column_id or column.name,
                    "current": {
                        "name": column.name,
                        "description": column.description,
                        "dtype": column.dtype,
                    },
                    "validator": {
                        "confidence": validation.confidence,
                        "reasons": list(validation.reasons),
                    },
                    "profile": validation.profile,
                }
            )
    fields.sort(key=lambda field: float(field["validator"]["confidence"]))
    return fields[:limit]


async def _targeted_reask_low_confidence_fields(
    *,
    llm_client: LLMClient,
    result: DiscoveryResult,
    files: list[File],
    validations: dict[tuple[str, str], ColumnValidation],
    settings: Settings,
    config: DiscoveryProcessingConfig,
) -> dict[tuple[str, str], ColumnValidation]:
    fields = _low_confidence_reask_payload(
        result=result,
        validations=validations,
        threshold=settings.schema_confidence_threshold,
        limit=settings.discovery_reask_max_fields,
    )
    if not fields:
        return validations

    originals: dict[tuple[str, str], DiscoveredColumn] = {}
    for discovered in result.files:
        for column in discovered.columns:
            key = _column_key(discovered.file_id, column)
            if any(
                field["file_id"] == discovered.file_id
                and field["column_id"] == (column.column_id or column.name)
                for field in fields
            ):
                originals[key] = DiscoveredColumn(
                    column_id=column.column_id,
                    name=column.name,
                    description=column.description,
                    dtype=column.dtype,
                )

    payload = await _respond_json_schema_with_retry(
        llm_client=llm_client,
        input_payload=[
            {
                "role": "user",
                "content": "Re-check only these low-confidence fields:\n"
                f"{json.dumps(fields, default=str, indent=2)}",
            }
        ],
        instructions=REASK_PROMPT,
        model=config.analysis_model,
        reasoning_effort=config.analysis_reasoning_effort,
        reasoning_summary=config.reasoning_summary,
        response_format={
            "type": "json_schema",
            "name": "discovery_recheck",
            "schema": REASK_RESPONSE_SCHEMA,
            "strict": True,
        },
    )
    _apply_reask_corrections(result, payload)
    updated = _validate_discovery_columns(files=files, discovered_files=result.files)
    for key, original in originals.items():
        before = validations.get(key)
        after = updated.get(key)
        if before is None or after is None or after.confidence >= settings.schema_confidence_threshold:
            continue
        if after.confidence <= before.confidence:
            _restore_column(result, key, original)
    return _validate_discovery_columns(files=files, discovered_files=result.files)


def _apply_reask_corrections(result: DiscoveryResult, payload: dict[str, Any]) -> None:
    corrections = payload.get("corrections")
    if not isinstance(corrections, list):
        return
    by_file = {file.file_id: file for file in result.files}
    allowed = {"string", "int", "float", "date", "datetime", "bool", "category"}
    for item in corrections:
        if not isinstance(item, dict):
            continue
        discovered = by_file.get(str(item.get("file_id") or ""))
        if discovered is None:
            continue
        column_id = str(item.get("column_id") or "").strip().lower()
        column = next(
            (
                col
                for col in discovered.columns
                if (col.column_id or col.name).strip().lower() == column_id
            ),
            None,
        )
        if column is None:
            continue
        dtype = str(item.get("dtype") or column.dtype)
        if dtype not in allowed:
            continue
        column.name = str(item.get("name") or column.name)
        column.description = str(item.get("description") or column.description)
        column.dtype = dtype


def _restore_column(
    result: DiscoveryResult, key: tuple[str, str], original: DiscoveredColumn
) -> None:
    file_id, column_key = key
    discovered = next((file for file in result.files if file.file_id == file_id), None)
    if discovered is None:
        return
    column = next(
        (
            col
            for col in discovered.columns
            if (col.column_id or col.name).strip().lower() == column_key
        ),
        None,
    )
    if column is None:
        return
    column.column_id = original.column_id
    column.name = original.name
    column.description = original.description
    column.dtype = original.dtype


def _column_validation(
    validations: dict[tuple[str, str], ColumnValidation] | None,
    file_id: str,
    column: DiscoveredColumn,
) -> ColumnValidation | None:
    if validations is None:
        return None
    return validations.get(_column_key(file_id, column))


def _column_values_for_relationships(
    file: File, discovered: DiscoveredFile, col_idx: int
) -> list[Any]:
    if not file.raw_parquet_path or col_idx >= len(discovered.columns):
        return []
    rows = pl.read_parquet(file.raw_parquet_path).to_numpy().tolist()
    data_rows = rows[discovered.header_row + 1 :]
    values: list[Any] = []
    for row in data_rows:
        if col_idx >= len(row):
            continue
        values.append(row[col_idx])
        if len(values) >= LINK_VALUE_SAMPLE_LIMIT:
            break
    return values


def _relationship_columns(
    *, files: list[File], discovered_files: list[DiscoveredFile]
) -> list[RelationshipColumn]:
    files_by_id = {file.id: file for file in files}
    columns: list[RelationshipColumn] = []
    for discovered in discovered_files:
        file = files_by_id.get(discovered.file_id)
        if file is None:
            continue
        file_name = discovered.friendly_name or file.filename
        for idx, col in enumerate(discovered.columns):
            values = _column_values_for_relationships(file, discovered, idx)
            columns.append(
                build_relationship_column(
                    file_id=discovered.file_id,
                    file_name=file_name,
                    column_name=col.name,
                    dtype=col.dtype,
                    values=values,
                )
            )
    return columns


def _find_relationship_candidates(
    *, files: list[File], discovered_files: list[DiscoveredFile], settings: Settings
) -> list[RelationshipCandidate]:
    return score_relationship_candidates(
        _relationship_columns(files=files, discovered_files=discovered_files),
        _relationship_scoring_config(settings),
    )


async def _verify_relationship_candidates(
    *,
    llm_client: LLMClient,
    candidates: list[RelationshipCandidate],
    config: DiscoveryProcessingConfig,
) -> list[DiscoveredLink]:
    if not candidates:
        return []
    candidate_payload = [candidate.verifier_payload() for candidate in candidates]
    payload = await _respond_json_schema_with_retry(
        llm_client=llm_client,
        input_payload=[
            {
                "role": "user",
                "content": "Verify these relationship candidates:\n"
                f"{json.dumps(candidate_payload, default=str, indent=2)}",
            }
        ],
        instructions=LINK_VERIFIER_PROMPT,
        model=config.analysis_model,
        reasoning_effort=config.analysis_reasoning_effort,
        reasoning_summary=config.reasoning_summary,
        response_format={
            "type": "json_schema",
            "name": "link_verification",
            "schema": LINK_VERIFIER_RESPONSE_SCHEMA,
            "strict": True,
        },
    )
    raw_verifications = payload.get("verifications")
    if not isinstance(raw_verifications, list):
        return []
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    verified: list[DiscoveredLink] = []
    seen: set[frozenset[tuple[str, str]]] = set()
    for item in raw_verifications:
        if not isinstance(item, dict) or not item.get("confirmed"):
            continue
        candidate = candidates_by_id.get(str(item.get("candidate_id") or ""))
        if candidate is None:
            continue
        key = _link_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        direction = cast(LinkDirection, item.get("direction") or candidate.direction)
        verified.append(
            DiscoveredLink(
                file_a_id=candidate.child_file_id,
                col_a=candidate.child_column,
                file_b_id=candidate.parent_file_id,
                col_b=candidate.parent_column,
                direction=direction,
                summary=str(item.get("summary") or candidate.summary),
            )
        )
    return verified


async def run_discovery(
    *,
    session_id: str,
    settings: Settings,
    files_repo: FileRepository,
    sessions_repo: SessionRepository,
    links_repo: LinkRepository,
    data_docs_repo: DataDocRepository,
    events_repo: ProcessingEventRepository,
    llm_client: LLMClient,
    schemas_repo: SchemaRepository | None = None,
    artifacts_repo: AssetArtifactRepository | None = None,
    object_store: ObjectStore | None = None,
    job_id: str | None = None,
    user_id: str | None = None,
    clear_events: bool = True,
) -> DiscoveryResult:
    if not settings.llm_api_key:
        raise DiscoveryError("discovery requires an LLM — set CERNO_LLM_API_KEY")
    discovery_config = settings.processing.discovery

    logger.info(
        "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=start model=%s reasoning=%s progress=5",
        user_id,
        session_id,
        job_id,
        discovery_config.model,
        discovery_config.reasoning_effort,
    )
    sessions_repo.set_discovery_status(session_id, "discovering")
    if clear_events:
        events_repo.clear(session_id)
    events_repo.append(
        session_id=session_id,
        job_id=job_id,
        kind="started",
        step_key="reading_files",
        level="info",
        progress=5,
        message="Reading your files",
    )

    files = files_repo.list_for_session(session_id)
    if not files:
        raise DiscoveryError("no files in session")
    logger.info(
        "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=files_loaded file_count=%s row_count=%s progress=10",
        user_id,
        session_id,
        job_id,
        len(files),
        sum(f.row_count for f in files),
    )
    if artifacts_repo and object_store:
        for f in files:
            cached = ensure_file_artifact_cached(
                file=f,
                artifact_type="raw_parquet",
                settings=settings,
                artifacts_repo=artifacts_repo,
                object_store=object_store,
            )
            if cached:
                f.raw_parquet_path = cached

    events_repo.append(
        session_id=session_id,
        job_id=job_id,
        kind="reading_files",
        step_key="reading_files",
        level="info",
        progress=15,
        message=f"Loading {len(files)} file(s)",
    )

    raw_tables, raw_catalog = _load_raw_tables(files)

    # ── Phase 2: Deterministic profiling (no LLM needed) ──
    events_repo.append(
        session_id=session_id,
        job_id=job_id,
        kind="python_analysis",
        step_key="profiling_columns",
        level="info",
        progress=20,
        message="Profiling columns and data types",
    )
    logger.info(
        "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=profiling_tables progress=20",
        user_id,
        session_id,
        job_id,
    )
    profiles = _profile_all_tables(raw_tables)
    files_by_id = {file.id: file for file in files}
    file_payload: list[dict[str, Any]] = []
    for entry in raw_catalog:
        payload_file = files_by_id[str(entry["file_id"])]
        if not payload_file.raw_parquet_path:
            raise DiscoveryError(f"file {payload_file.filename} missing raw parquet")
        file_payload.append(
            {
                "file_id": payload_file.id,
                "filename": payload_file.filename,
                "row_count": payload_file.row_count,
                "table_name": entry["table_name"],
                "column_profiles": profiles[str(entry["table_name"])]["columns"],
                "example_rows": first_n_raw_rows(
                    Path(payload_file.raw_parquet_path), limit=LAYOUT_SAMPLE_ROWS
                ),
            }
        )

    events_repo.append(
        session_id=session_id,
        job_id=job_id,
        kind="python_analysis",
        step_key="profiling_columns",
        level="info",
        progress=28,
        message="Looking for shared identifiers across files",
    )
    logger.info(
        "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=finding_overlaps progress=28",
        user_id,
        session_id,
        job_id,
    )
    logger.info(
        "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=profiling_done progress=30",
        user_id,
        session_id,
        job_id,
    )

    # ── Phase 3: Single LLM call to summarize profiles ──
    events_repo.append(
        session_id=session_id,
        job_id=job_id,
        kind="python_analysis",
        step_key="understanding_structure",
        level="info",
        progress=32,
        message="Interpreting column roles and relationships",
    )
    try:
        logger.info(
            "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=analysis_summary model=%s progress=32",
            user_id,
            session_id,
            job_id,
            discovery_config.analysis_model,
        )
        analysis = await _summarize_analysis(
            llm_client=llm_client,
            profiles=profiles,
            catalog=raw_catalog,
            config=discovery_config,
        )
        logger.info(
            "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=analysis_summary_done progress=50",
            user_id,
            session_id,
            job_id,
        )
    except Exception as exc:
        logger.exception(
            "event=processing.failed user_id=%s session_id=%s job_id=%s phase=analysis_summary",
            user_id,
            session_id,
            job_id,
        )
        events_repo.append(
            session_id=session_id,
            job_id=job_id,
            kind="error",
            step_key="error",
            level="error",
            progress=100,
            message="Failed to interpret file structure. Please try again.",
        )
        sessions_repo.set_discovery_status(session_id, "failed")
        raise DiscoveryError(f"Analysis summary failed: {exc}") from exc

    # ── Phase 4: Full schema generation via LLM ──
    user_prompt = _build_prompt(file_payload, analysis)
    events_repo.append(
        session_id=session_id,
        job_id=job_id,
        kind="calling_llm",
        step_key="building_data_map",
        level="info",
        progress=55,
        message="Building the data map — naming files and explaining columns",
    )

    try:
        logger.info(
            "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=llm_schema model=%s progress=55",
            user_id,
            session_id,
            job_id,
            discovery_config.model,
        )
        payload = await _respond_json_schema_with_retry(
            llm_client=llm_client,
            input_payload=[{"role": "user", "content": user_prompt}],
            instructions=SYSTEM_PROMPT,
            model=discovery_config.model,
            reasoning_effort=discovery_config.reasoning_effort,
            reasoning_summary=discovery_config.reasoning_summary,
            response_format={
                "type": "json_schema",
                "name": "discovery",
                "schema": RESPONSE_SCHEMA,
                "strict": True,
            },
        )
        logger.info(
            "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=llm_schema_done progress=80",
            user_id,
            session_id,
            job_id,
        )
    except Exception as exc:
        logger.exception(
            "event=processing.failed user_id=%s session_id=%s job_id=%s phase=llm_schema",
            user_id,
            session_id,
            job_id,
        )
        events_repo.append(
            session_id=session_id,
            job_id=job_id,
            kind="error",
            step_key="error",
            level="error",
            progress=100,
            message="Failed to build the data map. Please try again.",
        )
        sessions_repo.set_discovery_status(session_id, "failed")
        raise DiscoveryError(f"LLM call failed: {exc}") from exc

    # ── Phase 5: Parse, validate, and save ──
    events_repo.append(
        session_id=session_id,
        job_id=job_id,
        kind="saving_schema",
        step_key="mapping_connections",
        level="info",
        progress=85,
        message="Verifying connections and saving the data map",
    )
    logger.info(
        "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=parsing_response progress=85",
        user_id,
        session_id,
        job_id,
    )
    result = _parse_response(payload)
    result.links = []
    result.documentation["relationships"] = []

    valid_ids = {f.id for f in files}
    result.files = [df for df in result.files if df.file_id in valid_ids]
    if not result.files:
        logger.warning(
            "event=processing.failed user_id=%s session_id=%s job_id=%s phase=no_valid_schema_files",
            user_id,
            session_id,
            job_id,
        )
        sessions_repo.set_discovery_status(session_id, "failed")
        raise DiscoveryError("LLM did not return schema for any uploaded file")

    validations = _validate_discovery_columns(files=files, discovered_files=result.files)
    low_confidence_count = sum(
        validation.confidence < settings.schema_confidence_threshold
        for validation in validations.values()
    )
    if low_confidence_count:
        events_repo.append(
            session_id=session_id,
            job_id=job_id,
            kind="calling_llm",
            step_key="rechecking_low_confidence_fields",
            level="info",
            progress=88,
            message=f"Rechecking {min(low_confidence_count, settings.discovery_reask_max_fields)} low-confidence field(s)",
        )
        try:
            validations = await _targeted_reask_low_confidence_fields(
                llm_client=llm_client,
                result=result,
                files=files,
                validations=validations,
                settings=settings,
                config=discovery_config,
            )
        except Exception:
            logger.exception(
                "event=processing.warning user_id=%s session_id=%s job_id=%s phase=field_recheck_failed",
                user_id,
                session_id,
                job_id,
            )
            events_repo.append(
                session_id=session_id,
                job_id=job_id,
                kind="python_analysis",
                step_key="rechecking_low_confidence_fields",
                level="warning",
                progress=88,
                message="Some low-confidence fields could not be rechecked automatically",
            )

    schema_writer = schemas_repo or SchemaRepository(files_repo.conn)
    for df in result.files:
        files_repo.set_metadata(
            file_id=df.file_id,
            header_row=df.header_row,
            friendly_name=df.friendly_name,
            description=df.description,
        )
        file = next((f for f in files if f.id == df.file_id), None)
        if file is not None:
            schema_writer.replace(
                _schema_from_discovered_file(
                    df,
                    file.schema_version,
                    validations=validations,
                )
            )

    candidates = _find_relationship_candidates(
        files=files, discovered_files=result.files, settings=settings
    )
    events_repo.append(
        session_id=session_id,
        job_id=job_id,
        kind="calling_llm",
        step_key="verifying_relationships",
        level="info",
        progress=90,
        message="Verifying relationship candidates",
    )
    try:
        result.links = await _verify_relationship_candidates(
            llm_client=llm_client,
            candidates=candidates,
            config=discovery_config,
        )
    except Exception:
        logger.exception(
            "event=processing.warning user_id=%s session_id=%s job_id=%s phase=link_verification",
            user_id,
            session_id,
            job_id,
        )
        events_repo.append(
            session_id=session_id,
            job_id=job_id,
            kind="error",
            step_key="verifying_relationships",
            level="warning",
            progress=92,
            message="Relationship verification failed; continuing without automatic links",
        )
        result.links = []
    logger.info(
        "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=resolving_links candidate_count=%s link_count=%s progress=95",
        user_id,
        session_id,
        job_id,
        len(candidates),
        len(result.links),
    )

    links_repo.delete_for_session(session_id)
    candidate_by_key = {_link_key(candidate): candidate for candidate in candidates}
    for link in result.links:
        if link.file_a_id not in valid_ids or link.file_b_id not in valid_ids:
            continue
        candidate = candidate_by_key.get(_link_key(link))
        links_repo.create(
            session_id=session_id,
            file_a=link.file_a_id,
            col_a=link.col_a,
            file_b=link.file_b_id,
            col_b=link.col_b,
            overlap=candidate.containment if candidate else 1.0,
            direction=link.direction,
            score=candidate.score if candidate else 1.0,
            summary=link.summary or None,
        )

    sessions_repo.set_overview(session_id, result.overview)
    data_docs_repo.replace(
        _data_doc_from_result(
            session_id,
            result,
            validations=validations,
            link_candidates=candidate_by_key,
        )
    )
    sessions_repo.set_discovery_status(session_id, "pending_review")
    events_repo.append(
        session_id=session_id,
        job_id=job_id,
        kind="done",
        step_key="done",
        level="info",
        progress=100,
        message="Your data map is ready for review",
    )
    logger.info(
        "event=processing.complete user_id=%s session_id=%s job_id=%s file_count=%s link_count=%s progress=100",
        user_id,
        session_id,
        job_id,
        len(result.files),
        len(result.links),
    )
    return result


def _schema_from_discovered_file(
    discovered: DiscoveredFile,
    schema_version: int,
    validations: dict[tuple[str, str], ColumnValidation] | None = None,
) -> FileSchema:
    return FileSchema(
        file_id=discovered.file_id,
        schema_version=schema_version,
        columns=[
            SchemaColumn(
                file_id=discovered.file_id,
                schema_version=schema_version,
                name=column.name,
                dtype=_normalize_simple_dtype(column.dtype),
                inferred_kind=_inferred_kind_for_dtype(column.dtype),
                confidence=(
                    validation.confidence
                    if (validation := _column_validation(validations, discovered.file_id, column))
                    else 1.0
                ),
                position=index,
                column_id=column.column_id or column.name,
                description=column.description,
                confidence_reason=(
                    validation.encoded_reasons()
                    if (validation := _column_validation(validations, discovered.file_id, column))
                    else None
                ),
            )
            for index, column in enumerate(discovered.columns)
        ],
    )


def _normalize_simple_dtype(dtype: str) -> str:
    kind = _inferred_kind_for_dtype(dtype)
    return "int" if kind == "int" else kind


def _inferred_kind_for_dtype(dtype: str) -> InferredKind:
    return SIMPLE_DTYPE_TO_INFERRED_KIND.get(dtype.strip().lower(), "string")
