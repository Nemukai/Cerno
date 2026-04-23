from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast

import polars as pl

from cerno.config import Settings
from cerno.llm import LLMClient
from cerno.models import File, FileSchema, InferredKind, Link, LinkDirection
from cerno.repositories import FileRepository, LinkRepository, SchemaRepository

JOIN_CANDIDATE_KINDS: set[InferredKind] = {"int", "string", "category"}


class LinkDiscoveryError(RuntimeError):
    pass


@dataclass
class ColumnProfile:
    file_id: str
    filename: str
    column: str
    inferred_kind: InferredKind
    distinct_values: set[str]
    distinct_count: int
    total_count: int
    avg_length: float


@dataclass
class LinkCandidate:
    file_a: File
    col_a: str
    file_b: File
    col_b: str
    intersect_size: int
    containment_a_in_b: float
    containment_b_in_a: float
    direction: LinkDirection
    score: float


def _normalize_series(series: pl.Series) -> pl.Series:
    str_series = series.drop_nulls().cast(pl.String).str.strip_chars()
    return str_series.filter(str_series.str.len_chars() > 0)


def profile_column(
    frame: pl.DataFrame, file: File, col_name: str, kind: InferredKind
) -> ColumnProfile | None:
    if col_name not in frame.columns:
        return None
    normalized = _normalize_series(frame[col_name])
    total = normalized.len()
    if total == 0:
        return None
    distinct = normalized.unique().to_list()
    mean_len: Any = normalized.str.len_chars().mean()
    avg_length = float(mean_len) if isinstance(mean_len, (int, float)) else 0.0
    return ColumnProfile(
        file_id=file.id,
        filename=file.filename,
        column=col_name,
        inferred_kind=kind,
        distinct_values=set(distinct),
        distinct_count=len(distinct),
        total_count=total,
        avg_length=avg_length,
    )


def build_column_profiles(
    *,
    files: list[File],
    schemas: dict[str, FileSchema],
    frames: dict[str, pl.DataFrame],
    settings: Settings,
) -> list[ColumnProfile]:
    profiles: list[ColumnProfile] = []
    for file in files:
        schema = schemas[file.id]
        frame = frames[file.id]
        for col in schema.columns:
            if col.inferred_kind not in JOIN_CANDIDATE_KINDS:
                continue
            profile = profile_column(frame, file, col.name, col.inferred_kind)
            if profile is None:
                continue
            if profile.distinct_count < settings.link_min_distinct:
                continue
            if profile.avg_length > settings.link_max_avg_length:
                continue
            profiles.append(profile)
    return profiles


def _infer_direction(
    a: ColumnProfile, b: ColumnProfile
) -> tuple[ColumnProfile, ColumnProfile, LinkDirection]:
    a_unique = a.distinct_count == a.total_count
    b_unique = b.distinct_count == b.total_count
    if a_unique and b_unique:
        return a, b, "one_to_one"
    if b_unique:
        return a, b, "many_to_one"
    if a_unique:
        return b, a, "many_to_one"
    return a, b, "many_to_many"


def compute_candidates(
    *,
    profiles: list[ColumnProfile],
    files_by_id: dict[str, File],
    settings: Settings,
) -> list[LinkCandidate]:
    candidates: list[LinkCandidate] = []
    for i, a in enumerate(profiles):
        for b in profiles[i + 1 :]:
            if a.file_id == b.file_id:
                continue
            intersect = a.distinct_values & b.distinct_values
            if not intersect:
                continue
            cont_a_in_b = len(intersect) / a.distinct_count
            cont_b_in_a = len(intersect) / b.distinct_count
            if max(cont_a_in_b, cont_b_in_a) < settings.link_overlap_threshold:
                continue
            left, right, direction = _infer_direction(a, b)
            if left is a:
                left_cont, right_cont = cont_a_in_b, cont_b_in_a
            else:
                left_cont, right_cont = cont_b_in_a, cont_a_in_b
            candidates.append(
                LinkCandidate(
                    file_a=files_by_id[left.file_id],
                    col_a=left.column,
                    file_b=files_by_id[right.file_id],
                    col_b=right.column,
                    intersect_size=len(intersect),
                    containment_a_in_b=left_cont,
                    containment_b_in_a=right_cont,
                    direction=direction,
                    score=max(left_cont, right_cont),
                )
            )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def build_session_prompt(
    *,
    files: list[File],
    schemas: dict[str, FileSchema],
    frames: dict[str, pl.DataFrame],
    candidates: list[LinkCandidate],
    settings: Settings,
) -> list[dict[str, Any]]:
    file_cards: list[dict[str, Any]] = []
    for file in files:
        schema = schemas[file.id]
        frame = frames[file.id]
        sample = frame.head(settings.link_sample_rows).to_dicts()
        file_cards.append(
            {
                "file_id": file.id,
                "filename": file.filename,
                "row_count": file.row_count,
                "columns": [
                    {
                        "name": c.name,
                        "inferred_kind": c.inferred_kind,
                        "confidence": round(c.confidence, 2),
                    }
                    for c in schema.columns
                ],
                "sample_rows": sample,
            }
        )
    candidate_list = [
        {
            "file_a_id": c.file_a.id,
            "file_a_name": c.file_a.filename,
            "col_a": c.col_a,
            "file_b_id": c.file_b.id,
            "file_b_name": c.file_b.filename,
            "col_b": c.col_b,
            "overlap_stats": {
                "intersect_size": c.intersect_size,
                "containment_a_in_b": round(c.containment_a_in_b, 3),
                "containment_b_in_a": round(c.containment_b_in_a, 3),
            },
            "proposed_direction": c.direction,
        }
        for c in candidates
    ]
    payload = {"files": file_cards, "candidates": candidate_list}
    system = (
        "You are Cerno's link discovery engine. You decide which column-pair candidates "
        "represent real relationships between files (shared entity IDs, foreign keys, "
        "denormalized references). Use the file names, column headers, inferred kinds, "
        "and sample rows to understand what each column means. Reject coincidental overlaps "
        "(e.g. shared low-cardinality categories). For each real link, write a single "
        "plain-English sentence explaining the relationship."
    )
    user = (
        "Decide which of the candidate column pairs are real links, confirm or correct the "
        "proposed direction, and write a plain-English summary for each.\n\n"
        "Return STRICT JSON with this shape and no prose:\n"
        '{"links": [{"file_a_id": str, "col_a": str, "file_b_id": str, "col_b": str, '
        '"direction": "many_to_one" | "one_to_one" | "many_to_many", '
        '"summary": str, "confidence": float}]}\n\n'
        f"Session data:\n{json.dumps(payload, default=str)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_llm_response(content: str) -> list[dict[str, Any]]:
    stripped = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        parsed: dict[str, Any] = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise LinkDiscoveryError(f"LLM returned invalid JSON: {exc}") from exc
    links = parsed.get("links")
    if not isinstance(links, list):
        raise LinkDiscoveryError("LLM response missing 'links' array")
    return cast(list[dict[str, Any]], links)


async def discover_links(
    *,
    session_id: str,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    links_repo: LinkRepository,
    llm_client: LLMClient,
) -> list[Link]:
    if not settings.llm_api_key:
        raise LinkDiscoveryError(
            "link discovery requires an LLM — set CERNO_LLM_API_KEY"
        )

    files = files_repo.list_for_session(session_id)
    if len(files) < 2:
        return []

    schemas: dict[str, FileSchema] = {}
    frames: dict[str, pl.DataFrame] = {}
    for file in files:
        schema = schemas_repo.get(file.id, file.schema_version)
        if schema is None:
            raise LinkDiscoveryError(f"no schema for file {file.id}")
        schemas[file.id] = schema
        frames[file.id] = pl.read_parquet(file.parquet_path)

    files_by_id = {f.id: f for f in files}
    profiles = build_column_profiles(
        files=files, schemas=schemas, frames=frames, settings=settings
    )
    candidates = compute_candidates(
        profiles=profiles, files_by_id=files_by_id, settings=settings
    )
    if not candidates:
        return []

    messages = build_session_prompt(
        files=files, schemas=schemas, frames=frames, candidates=candidates, settings=settings
    )
    response = await llm_client.complete(messages=messages, temperature=0.1)
    entries = parse_llm_response(response.content)

    cand_index: dict[tuple[str, str, str, str], LinkCandidate] = {
        (c.file_a.id, c.col_a, c.file_b.id, c.col_b): c for c in candidates
    }
    created: list[Link] = []
    for entry in entries:
        key = (
            entry.get("file_a_id", ""),
            entry.get("col_a", ""),
            entry.get("file_b_id", ""),
            entry.get("col_b", ""),
        )
        cand = cand_index.get(key)
        swapped = False
        if cand is None:
            reverse = (key[2], key[3], key[0], key[1])
            cand = cand_index.get(reverse)
            if cand is None:
                continue
            swapped = True

        file_a_id = cand.file_a.id
        col_a = cand.col_a
        file_b_id = cand.file_b.id
        col_b = cand.col_b
        direction = cast(LinkDirection, entry.get("direction") or cand.direction)
        if swapped and direction == "many_to_one":
            direction = "many_to_one"
        confidence = float(entry.get("confidence") or cand.score)
        summary = str(entry.get("summary") or "").strip() or None

        link = links_repo.create(
            session_id=session_id,
            file_a=file_a_id,
            col_a=col_a,
            file_b=file_b_id,
            col_b=col_b,
            overlap=max(cand.containment_a_in_b, cand.containment_b_in_a),
            direction=direction,
            score=confidence,
            summary=summary,
        )
        created.append(link)
    return created
