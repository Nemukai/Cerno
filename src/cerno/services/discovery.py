from __future__ import annotations

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
    LinkDirection,
)
from cerno.repositories import (
    AssetArtifactRepository,
    DataDocRepository,
    FileRepository,
    LinkRepository,
    ProcessingEventRepository,
    SessionRepository,
)
from cerno.services.artifact_cache import ensure_file_artifact_cached
from cerno.services.ingest import first_n_raw_rows, slugify_table_name
from cerno.storage import ObjectStore

SAMPLE_ROWS = 10
LINK_VALUE_SAMPLE_LIMIT = 5_000
logger = logging.getLogger(__name__)


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


@dataclass
class LinkCandidate:
    file_a_id: str
    col_a: str
    file_b_id: str
    col_b: str
    direction: LinkDirection
    overlap: float
    score: float
    summary: str


SYSTEM_PROMPT = """You are Cerno's highly capable data discovery engine. Tabular files have been uploaded for analysis.
Your job is to deeply analyze the raw first rows and the Python analysis notes, then systematically determine:

1. The exact header row (0-indexed). Account for files with title rows, blank rows, or metadata before actual headers.
2. A clean, human-friendly business name for each file (e.g., 'Customer Orders', 'Inventory Log'). Omit extensions and raw timestamps.
3. A concise, non-technical 1-2 sentence description of the file's primary purpose and grain (what one row represents).
4. The exact schema: column names (post-header), plain-language descriptions, and the correct data type (string, int, float, date, datetime, bool, category).

Next, synthesize cross-file relationships. Identify strong identifiers (e.g. transaction_id, user_id, sku) and declare links.
For each link, write a clear 1-sentence explanation of how they join, and establish the direction (one_to_one, many_to_one, many_to_many).
Prioritize meaningful business keys over coincidental low-cardinality matches.

Finally, construct the internal DataDoc (documentation):
- Write a 2-4 sentence cohesive overview of the entire workspace and how the files interconnect.
- Ensure the 'grain' and 'caveats' for each file are well-documented.
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
        "and plain-language description. Then identify "
        "cross-file column links, write a session overview, and create the internal "
        "documentation used by the chat analyst.\n\n"
        "Use the Python analysis notes as higher-confidence evidence than the sample "
        "rows when they conflict.\n\n"
        "Return STRICT JSON matching the provided schema. No prose, no fences.\n\n"
        f"Python analysis notes:\n{analysis}\n\n"
        f"Files:\n{json.dumps(file_payload, default=str, indent=2)}"
    )


ANALYSIS_SUMMARY_PROMPT = """You are Cerno's internal data profiler. Below are pre-computed column profiles and
shared-identifier overlaps for every uploaded file. Synthesise them into concise analysis notes covering:

- File Purpose & Grain: What does each file represent? What does one row mean?
- Schema Reality: Likely header row index (0 = first row is already headers). Are there title/metadata rows to skip?
- Column Roles: Primary keys/IDs, date columns, key measures, categoricals, and highly null columns.
- Relationships: Which shared identifiers look like real foreign-key joins vs. coincidental overlap?
- Data Quality & Caveats: Missing data patterns or gotchas a non-technical user must know.

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


def _profile_table(df: pd.DataFrame) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for col in df.columns:
        series = df[col]
        nulls = int(series.isnull().sum())
        distinct = int(series.nunique())
        stats[str(col)] = {
            "dtype": str(series.dtype),
            "nulls": nulls,
            "null_pct": round(nulls / len(df) * 100, 1) if len(df) > 0 else 0,
            "distinct": distinct,
            "sample": [str(x) for x in series.dropna().unique()[:3]],
        }
    return {"row_count": len(df), "columns": stats}


def _profile_all_tables(
    raw_tables: dict[str, pd.DataFrame],
) -> dict[str, dict[str, Any]]:
    return {name: _profile_table(df) for name, df in raw_tables.items()}


def _find_shared_identifiers(
    df_a: pd.DataFrame, df_b: pd.DataFrame
) -> list[dict[str, Any]]:
    value_cache_a: dict[str, set[str]] = {}
    value_cache_b: dict[str, set[str]] = {}
    results: list[dict[str, Any]] = []
    for col_a in df_a.columns:
        if col_a not in value_cache_a:
            value_cache_a[col_a] = set(df_a[col_a].dropna().astype(str).unique())
        set_a = value_cache_a[col_a]
        if len(set_a) < 2:
            continue
        for col_b in df_b.columns:
            if col_b not in value_cache_b:
                value_cache_b[col_b] = set(df_b[col_b].dropna().astype(str).unique())
            set_b = value_cache_b[col_b]
            if len(set_b) < 2:
                continue

            intersection = set_a & set_b
            if not intersection:
                continue

            overlap_a = len(intersection) / len(set_a)
            overlap_b = len(intersection) / len(set_b)
            if overlap_a > 0.1 or overlap_b > 0.1:
                results.append(
                    {
                        "col_a": str(col_a),
                        "col_b": str(col_b),
                        "overlap_a": round(overlap_a, 3),
                        "overlap_b": round(overlap_b, 3),
                        "distinct_a": len(set_a),
                        "distinct_b": len(set_b),
                        "intersection_size": len(intersection),
                    }
                )
    results.sort(key=lambda x: max(x["overlap_a"], x["overlap_b"]), reverse=True)
    return results[:10]


def _find_all_overlaps(
    raw_tables: dict[str, pd.DataFrame],
) -> dict[str, list[dict[str, Any]]]:
    names = list(raw_tables)
    overlaps: dict[str, list[dict[str, Any]]] = {}
    for i, name_a in enumerate(names):
        for name_b in names[i + 1 :]:
            key = f"{name_a} ↔ {name_b}"
            links = _find_shared_identifiers(raw_tables[name_a], raw_tables[name_b])
            if links:
                overlaps[key] = links
    return overlaps


async def _summarize_analysis(
    *,
    llm_client: LLMClient,
    profiles: dict[str, dict[str, Any]],
    overlaps: dict[str, list[dict[str, Any]]],
    catalog: list[dict[str, Any]],
    config: DiscoveryProcessingConfig,
) -> str:
    user_content = (
        f"{ANALYSIS_SUMMARY_PROMPT}\n\n"
        f"Table catalog:\n{json.dumps(catalog, default=str, indent=2)}\n\n"
        f"Column profiles:\n{json.dumps(profiles, default=str, indent=2)}\n\n"
        f"Shared identifiers:\n{json.dumps(overlaps, default=str, indent=2)}"
    )
    response = await llm_client.respond(
        input=[{"role": "user", "content": user_content}],
        instructions=(
            "You are Cerno's internal data profiler. Write concise analysis notes "
            "about grain, column roles, and relationships based on the pre-computed "
            "profiles. Do not ask for tools or additional data."
        ),
        model=config.analysis_model,
        reasoning_effort=config.analysis_reasoning_effort,
        reasoning_summary=config.reasoning_summary,
    )
    return response.content


def _data_doc_from_result(session_id: str, result: DiscoveryResult) -> DataDoc:
    now = datetime.now(UTC)
    raw = result.documentation
    file_docs = [
        DataDocFile(
            file_id=str(item.get("file_id") or ""),
            name=str(item.get("name") or ""),
            description=str(item.get("description") or ""),
            grain=str(item.get("grain") or ""),
            row_count=int(item.get("row_count") or 0),
            columns=[
                DataDocColumn(
                    name=str(col.get("name") or ""),
                    dtype="auto",
                    meaning=str(col.get("meaning") or ""),
                    role="auto",
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
        DataDocRelationship(
            left_file_id=str(item.get("left_file_id") or ""),
            left_column=str(item.get("left_column") or ""),
            right_file_id=str(item.get("right_file_id") or ""),
            right_column=str(item.get("right_column") or ""),
            explanation=str(item.get("explanation") or ""),
        )
        for item in raw.get("relationships", [])
        if isinstance(item, dict)
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


def _normalized_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"nan", "none", "null"}:
        return None
    if len(text) > 100:
        return None
    return re.sub(r"\s+", " ", text)


def _values_for_column(file: File, discovered: DiscoveredFile, col_idx: int) -> set[str]:
    if not file.raw_parquet_path or col_idx >= len(discovered.columns):
        return set()
    rows = pl.read_parquet(file.raw_parquet_path).to_numpy().tolist()
    data_rows = rows[discovered.header_row + 1 :]
    values: set[str] = set()
    for row in data_rows:
        if col_idx >= len(row):
            continue
        normalized = _normalized_value(row[col_idx])
        if normalized is None:
            continue
        values.add(normalized)
        if len(values) >= LINK_VALUE_SAMPLE_LIMIT:
            break
    return values


def _looks_like_fk(left: str, right: str) -> bool:
    left_name = left.lower().replace(" ", "_")
    right_name = right.lower().replace(" ", "_")
    if right_name in {"id", "code", "number", "no"} and left_name.endswith(
        f"_{right_name}"
    ):
        return True
    if right_name.endswith("_id") and left_name == right_name:
        return True
    return False


def _candidate_direction(
    a_col: str,
    b_col: str,
    a_distinct: int,
    b_distinct: int,
    overlap_a: float,
    overlap_b: float,
) -> tuple[LinkDirection, bool]:
    if _looks_like_fk(a_col, b_col):
        return "many_to_one", False
    if _looks_like_fk(b_col, a_col):
        return "many_to_one", True
    if overlap_a >= 0.95 and overlap_b >= 0.95 and abs(a_distinct - b_distinct) <= 1:
        return "one_to_one", False
    if a_distinct > b_distinct and overlap_b >= 0.8:
        return "many_to_one", False
    if b_distinct > a_distinct and overlap_a >= 0.8:
        return "many_to_one", True
    return "many_to_many", False


def _link_key(link: DiscoveredLink | LinkCandidate) -> frozenset[tuple[str, str]]:
    return frozenset(
        {
            (link.file_a_id, link.col_a.lower()),
            (link.file_b_id, link.col_b.lower()),
        }
    )


def _find_overlap_candidates(
    *, files: list[File], discovered_files: list[DiscoveredFile], settings: Settings
) -> list[LinkCandidate]:
    files_by_id = {file.id: file for file in files}
    column_values: dict[tuple[str, str], set[str]] = {}

    for discovered in discovered_files:
        file = files_by_id.get(discovered.file_id)
        if file is None:
            continue
        for idx, col in enumerate(discovered.columns):
            column_values[(discovered.file_id, col.name)] = _values_for_column(
                file, discovered, idx
            )

    candidates: list[LinkCandidate] = []
    for a_idx, a_file in enumerate(discovered_files):
        for b_file in discovered_files[a_idx + 1 :]:
            for a_col in a_file.columns:
                a_values = column_values.get((a_file.file_id, a_col.name), set())
                if len(a_values) < settings.link_min_distinct:
                    continue
                for b_col in b_file.columns:
                    b_values = column_values.get((b_file.file_id, b_col.name), set())
                    if len(b_values) < settings.link_min_distinct:
                        continue
                    overlap = a_values & b_values
                    if not overlap:
                        continue
                    overlap_a = len(overlap) / len(a_values)
                    overlap_b = len(overlap) / len(b_values)
                    strength = max(overlap_a, overlap_b)
                    if strength < settings.link_overlap_threshold:
                        continue
                    direction, swap = _candidate_direction(
                        a_col.name,
                        b_col.name,
                        len(a_values),
                        len(b_values),
                        overlap_a,
                        overlap_b,
                    )
                    if swap:
                        source_file, source_col = b_file, b_col
                        target_file, target_col = a_file, a_col
                        overlap_score = overlap_b
                    else:
                        source_file, source_col = a_file, a_col
                        target_file, target_col = b_file, b_col
                        overlap_score = overlap_a
                    summary = (
                        f"{source_file.friendly_name}.{source_col.name} shares "
                        f"{len(overlap)} distinct values with "
                        f"{target_file.friendly_name}.{target_col.name}."
                    )
                    candidates.append(
                        LinkCandidate(
                            file_a_id=source_file.file_id,
                            col_a=source_col.name,
                            file_b_id=target_file.file_id,
                            col_b=target_col.name,
                            direction=direction,
                            overlap=overlap_score,
                            score=strength,
                            summary=summary,
                        )
                    )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _merge_links(
    links: list[DiscoveredLink], candidates: list[LinkCandidate]
) -> list[DiscoveredLink]:
    seen = {_link_key(link) for link in links}
    merged = list(links)
    for candidate in candidates:
        key = _link_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        merged.append(
            DiscoveredLink(
                file_a_id=candidate.file_a_id,
                col_a=candidate.col_a,
                file_b_id=candidate.file_b_id,
                col_b=candidate.col_b,
                direction=candidate.direction,
                summary=candidate.summary,
            )
        )
    return merged


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
                local_path=f.raw_parquet_path,
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
    file_payload: list[dict[str, Any]] = []
    for f in files:
        if not f.raw_parquet_path:
            raise DiscoveryError(f"file {f.filename} missing raw parquet")
        rows = first_n_raw_rows(Path(f.raw_parquet_path), limit=SAMPLE_ROWS)
        file_payload.append(
            {
                "file_id": f.id,
                "filename": f.filename,
                "row_count": f.row_count,
                "first_rows": rows,
            }
        )

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
    overlaps = _find_all_overlaps(raw_tables)
    logger.info(
        "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=profiling_done overlap_pairs=%s progress=30",
        user_id,
        session_id,
        job_id,
        len(overlaps),
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
            overlaps=overlaps,
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
        message="Building the data map — naming files, explaining columns, and checking connections",
    )

    try:
        logger.info(
            "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=llm_schema model=%s progress=55",
            user_id,
            session_id,
            job_id,
            discovery_config.model,
        )
        response = await llm_client.respond(
            input=[{"role": "user", "content": user_prompt}],
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
    payload = _parse_json_block(response.content)
    result = _parse_response(payload)

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

    for df in result.files:
        files_repo.set_metadata(
            file_id=df.file_id,
            header_row=df.header_row,
            friendly_name=df.friendly_name,
            description=df.description,
        )

    candidates = _find_overlap_candidates(
        files=files, discovered_files=result.files, settings=settings
    )
    result.links = _merge_links(result.links, candidates)
    logger.info(
        "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=resolving_links candidate_count=%s link_count=%s progress=95",
        user_id,
        session_id,
        job_id,
        len(candidates),
        len(result.links),
    )

    links_repo.delete_for_session(session_id)
    for link in result.links:
        if link.file_a_id not in valid_ids or link.file_b_id not in valid_ids:
            continue
        candidate = next((c for c in candidates if _link_key(c) == _link_key(link)), None)
        links_repo.create(
            session_id=session_id,
            file_a=link.file_a_id,
            col_a=link.col_a,
            file_b=link.file_b_id,
            col_b=link.col_b,
            overlap=candidate.overlap if candidate else 1.0,
            direction=link.direction,
            score=candidate.score if candidate else 1.0,
            summary=link.summary or None,
        )

    sessions_repo.set_overview(session_id, result.overview)
    data_docs_repo.replace(_data_doc_from_result(session_id, result))
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

