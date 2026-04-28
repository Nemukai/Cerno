from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from cerno.config import Settings
from cerno.llm import LLMClient
from cerno.models import LinkDirection
from cerno.repositories import (
    FileRepository,
    LinkRepository,
    ProcessingEventRepository,
    SessionRepository,
)
from cerno.services.ingest import first_n_raw_rows

DISCOVERY_MODEL = "gpt-5.4"
DISCOVERY_REASONING_EFFORT = "high"
DISCOVERY_REASONING_SUMMARY = "auto"
SAMPLE_ROWS = 10


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
    raw_response: dict[str, Any] = field(default_factory=dict)


SYSTEM_PROMPT = """You are Cerno's data discovery engine. Several tabular files have been uploaded.
Your job is to look at the raw first rows of each file and decide:

1. Which row is the header row (0-indexed). Spreadsheets often have title rows,
   blank rows, or report metadata before the actual column headers.
2. A short, human-friendly name for the file.
3. A 1-2 sentence description of what the file contains.
4. The columns: name (post-header), short description, and one of these data types:
   string, int, float, date, datetime, bool, category.

Then, looking across all files, identify column-to-column relationships
(shared identifiers, foreign keys, denormalized references). For each link explain it
in one plain sentence and pick a direction.

Finally, write a 2-4 sentence overview of how all the files relate to each other.

Be precise. Reject coincidental overlaps. Use the file names and the data to ground
your decisions. Return ONLY strict JSON conforming to the requested schema, no prose,
no fences."""


RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["files", "links", "overview"],
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
        raise DiscoveryError(f"LLM returned invalid JSON: {exc}\n--- raw ---\n{content[:2000]}") from exc


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
    return DiscoveryResult(files=files, links=links, overview=overview, raw_response=payload)


def _build_prompt(file_payload: list[dict[str, Any]]) -> str:
    return (
        "For each file below, decide the header row, a friendly name, a description, "
        "and the columns (post-header) with type and short description. Then identify "
        "cross-file column links and write a session overview.\n\n"
        "Return STRICT JSON matching the provided schema. No prose, no fences.\n\n"
        f"Files:\n{json.dumps(file_payload, default=str, indent=2)}"
    )


async def run_discovery(
    *,
    session_id: str,
    settings: Settings,
    files_repo: FileRepository,
    sessions_repo: SessionRepository,
    links_repo: LinkRepository,
    events_repo: ProcessingEventRepository,
    llm_client: LLMClient,
) -> DiscoveryResult:
    if not settings.llm_api_key:
        raise DiscoveryError("discovery requires an LLM — set CERNO_LLM_API_KEY")

    sessions_repo.set_discovery_status(session_id, "discovering")
    events_repo.clear(session_id)
    events_repo.append(session_id=session_id, kind="started", message="processing started")

    files = files_repo.list_for_session(session_id)
    if not files:
        raise DiscoveryError("no files in session")

    events_repo.append(
        session_id=session_id,
        kind="reading_files",
        message=f"reading first {SAMPLE_ROWS} rows of {len(files)} file(s)",
    )

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

    user_prompt = _build_prompt(file_payload)
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
        events_repo.append(
            session_id=session_id, kind="error", message=f"LLM call failed: {exc}"
        )
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

    links_repo.delete_for_session(session_id)
    for link in result.links:
        if link.file_a_id not in valid_ids or link.file_b_id not in valid_ids:
            continue
        links_repo.create(
            session_id=session_id,
            file_a=link.file_a_id,
            col_a=link.col_a,
            file_b=link.file_b_id,
            col_b=link.col_b,
            overlap=1.0,
            direction=link.direction,
            score=1.0,
            summary=link.summary or None,
        )

    sessions_repo.set_overview(session_id, result.overview)
    sessions_repo.set_discovery_status(session_id, "pending_review")
    events_repo.append(
        session_id=session_id,
        kind="done",
        message="discovery complete - review the schema then approve",
    )
    return result
