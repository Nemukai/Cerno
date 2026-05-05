from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
import polars as pl

from cerno.config import Settings
from cerno.llm import LLMClient, Tool, ToolRegistry, run_tool_loop
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
from cerno.services.sandbox import run_python
from cerno.storage import ObjectStore

DISCOVERY_MODEL = "gpt-5.5"
DISCOVERY_REASONING_EFFORT = "high"
DISCOVERY_REASONING_SUMMARY = "auto"
SAMPLE_ROWS = 10
LINK_VALUE_SAMPLE_LIMIT = 10_000


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


ANALYSIS_PROMPT = """You must inspect the full uploaded dataframes using the provided tools (profile_table, find_shared_identifiers).
If you need highly specific aggregations not covered by the standard tools, use run_python. Do not write files or use SQL.

Return concise, structured profiling notes covering:
- File Purpose & Grain: What does each file represent? What does one row mean?
- Schema Reality: Likely header row index. Are there title/metadata rows to skip?
- Column Profiling: Row counts, highly null columns, primary keys/IDs, date columns, key measures, and categoricals.
- Relationships: Concrete evidence of shared identifiers between files (use find_shared_identifiers).
- Data Quality & Caveats: Any anomalies, missing data patterns, or gotchas a non-technical user must know.
"""


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


def _build_analysis_registry(
    *, raw_tables: dict[str, pd.DataFrame], catalog: list[dict[str, Any]]
) -> ToolRegistry:
    registry = ToolRegistry()

    async def list_raw_tables(_args: dict[str, Any]) -> dict[str, Any]:
        return {"tables": catalog}

    async def run_python_handler(args: dict[str, Any]) -> dict[str, Any]:
        code = str(args["code"])
        return run_python(code, tables=raw_tables, timeout_seconds=20.0).to_dict()

    async def profile_table_handler(args: dict[str, Any]) -> dict[str, Any]:
        table_name = str(args.get("table_name", ""))
        df = raw_tables.get(table_name)
        if df is None:
            return {"error": f"Table {table_name} not found"}

        stats = {}
        for col in df.columns:
            series = df[col]
            nulls = int(series.isnull().sum())
            distinct = int(series.nunique())
            stats[str(col)] = {
                "dtype": str(series.dtype),
                "nulls": nulls,
                "null_pct": round(nulls / len(df) * 100, 1) if len(df) > 0 else 0,
                "distinct": distinct,
                "sample": [str(x) for x in series.dropna().unique()[:3]]
            }
        return {"row_count": len(df), "columns": stats}

    async def find_shared_identifiers_handler(args: dict[str, Any]) -> dict[str, Any]:
        table_a = str(args.get("table_a", ""))
        table_b = str(args.get("table_b", ""))
        df_a = raw_tables.get(table_a)
        df_b = raw_tables.get(table_b)
        if df_a is None or df_b is None:
            return {"error": "One or both tables not found"}

        results = []
        for col_a in df_a.columns:
            for col_b in df_b.columns:
                set_a = set(df_a[col_a].dropna().astype(str).unique())
                if not set_a or len(set_a) < 2:
                    continue
                set_b = set(df_b[col_b].dropna().astype(str).unique())
                if not set_b or len(set_b) < 2:
                    continue

                intersection = set_a.intersection(set_b)
                if not intersection:
                    continue

                overlap_a = len(intersection) / len(set_a)
                overlap_b = len(intersection) / len(set_b)

                if overlap_a > 0.1 or overlap_b > 0.1:
                    results.append({
                        "col_a": col_a,
                        "col_b": col_b,
                        "overlap_a": round(overlap_a, 3),
                        "overlap_b": round(overlap_b, 3),
                        "distinct_a": len(set_a),
                        "distinct_b": len(set_b),
                        "intersection_size": len(intersection)
                    })
        return {"potential_links": sorted(results, key=lambda x: max(x["overlap_a"], x["overlap_b"]), reverse=True)[:10]}

    registry.register(
        Tool(
            name="list_raw_tables",
            description="List the raw uploaded dataframes available for Python analysis.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=list_raw_tables,
        )
    )
    registry.register(
        Tool(
            name="run_python",
            description=(
                "Run Python against the full raw uploaded dataframes. Each dataframe is "
                "pre-bound by table_name from list_raw_tables. pd and np are available."
            ),
            parameters={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
                "additionalProperties": False,
            },
            handler=run_python_handler,
        )
    )
    registry.register(
        Tool(
            name="profile_table",
            description="Quickly get row counts, null percentages, distinct counts, and sample values for all columns in a table without writing Python code.",
            parameters={
                "type": "object",
                "properties": {"table_name": {"type": "string"}},
                "required": ["table_name"],
                "additionalProperties": False,
            },
            handler=profile_table_handler,
        )
    )
    registry.register(
        Tool(
            name="find_shared_identifiers",
            description="Automatically compute column value overlaps between two tables to find potential foreign keys or shared identifiers.",
            parameters={
                "type": "object",
                "properties": {
                    "table_a": {"type": "string"},
                    "table_b": {"type": "string"}
                },
                "required": ["table_a", "table_b"],
                "additionalProperties": False,
            },
            handler=find_shared_identifiers_handler,
        )
    )
    return registry


async def _analyze_with_python(
    *, llm_client: LLMClient, raw_tables: dict[str, pd.DataFrame], catalog: list[dict[str, Any]]
) -> str:
    result = await run_tool_loop(
        client=llm_client,
        registry=_build_analysis_registry(raw_tables=raw_tables, catalog=catalog),
        input=[
            {
                "role": "user",
                "content": (
                    f"{ANALYSIS_PROMPT}\n\nRaw table catalog:\n"
                    f"{json.dumps(catalog, default=str, indent=2)}"
                ),
            }
        ],
        instructions=(
            "You are Cerno's internal data profiler. Use the specialized tools (profile_table, find_shared_identifiers) "
            "to quickly inspect files. Use run_python only for complex or specific queries not covered by the standard tools. "
            "Write concise analysis notes about grain, column roles, and relationships."
        ),
        reasoning_effort=DISCOVERY_REASONING_EFFORT,
        reasoning_summary=DISCOVERY_REASONING_SUMMARY,
        max_calls=8,
    )
    return result.final_message


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
) -> DiscoveryResult:
    if not settings.llm_api_key:
        raise DiscoveryError("discovery requires an LLM — set CERNO_LLM_API_KEY")

    sessions_repo.set_discovery_status(session_id, "discovering")
    events_repo.clear(session_id)
    events_repo.append(session_id=session_id, kind="started", message="processing started")

    files = files_repo.list_for_session(session_id)
    if not files:
        raise DiscoveryError("no files in session")
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
        kind="reading_files",
        message=f"reading first {SAMPLE_ROWS} rows of {len(files)} file(s)",
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

    events_repo.append(
        session_id=session_id,
        kind="calling_llm",
        message="analyzing full files with Python",
    )
    try:
        analysis = await _analyze_with_python(
            llm_client=llm_client,
            raw_tables=raw_tables,
            catalog=raw_catalog,
        )
    except Exception as exc:
        events_repo.append(
            session_id=session_id, kind="error", message=f"Python analysis failed: {exc}"
        )
        sessions_repo.set_discovery_status(session_id, "failed")
        raise DiscoveryError(f"Python analysis failed: {exc}") from exc

    user_prompt = _build_prompt(file_payload, analysis)
    events_repo.append(
        session_id=session_id,
        kind="calling_llm",
        message=f"asking {DISCOVERY_MODEL} (reasoning={DISCOVERY_REASONING_EFFORT}). this can take a few minutes.",
    )

    try:
        response = await llm_client.respond(
            input=[{"role": "user", "content": user_prompt}],
            instructions=SYSTEM_PROMPT,
            model=DISCOVERY_MODEL,
            reasoning_effort=DISCOVERY_REASONING_EFFORT,
            reasoning_summary=DISCOVERY_REASONING_SUMMARY,
            response_format={
                "type": "json_schema",
                "name": "discovery",
                "schema": RESPONSE_SCHEMA,
                "strict": True,
            },
        )
    except Exception as exc:
        events_repo.append(session_id=session_id, kind="error", message=f"LLM call failed: {exc}")
        sessions_repo.set_discovery_status(session_id, "failed")
        raise DiscoveryError(f"LLM call failed: {exc}") from exc

    events_repo.append(
        session_id=session_id, kind="parsing_response", message="parsing LLM response"
    )
    payload = _parse_json_block(response.content)
    result = _parse_response(payload)

    valid_ids = {f.id for f in files}
    result.files = [df for df in result.files if df.file_id in valid_ids]
    if not result.files:
        sessions_repo.set_discovery_status(session_id, "failed")
        raise DiscoveryError("LLM did not return schema for any uploaded file")

    events_repo.append(
        session_id=session_id,
        kind="saving_schema",
        message=f"saving discovered schema for {len(result.files)} file(s)",
    )

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
        kind="done",
        message="discovery complete - review the schema then approve",
    )
    return result
