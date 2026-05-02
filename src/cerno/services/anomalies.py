from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import polars as pl

from cerno.config import Settings
from cerno.models import Anomaly, File, FileSchema
from cerno.repositories import (
    AnomalyRepository,
    FileRepository,
    LinkRepository,
    SchemaRepository,
    new_id,
)
from cerno.services.ingest import slugify_table_name

RARE_VALUE_KINDS = {"category", "string", "bool"}
NUMERIC_KINDS = {"int", "float"}


@dataclass
class AnomalyDraft:
    file_id: str
    row_id: int
    detector: str
    reason_plain: str
    reason_technical: str
    score_normalized: float
    score_raw: float
    source_code: str


def _load_frame(file: File) -> pl.DataFrame:
    return pl.read_parquet(file.parquet_path)


def _detect_numeric_mad(
    *, file: File, schema: FileSchema, frame: pl.DataFrame, settings: Settings
) -> list[AnomalyDraft]:
    drafts: list[AnomalyDraft] = []
    table = slugify_table_name(file.filename)
    threshold = settings.anomaly_mad_threshold
    for col in schema.columns:
        if col.inferred_kind not in NUMERIC_KINDS:
            continue
        if col.name not in frame.columns:
            continue
        series = frame[col.name]
        try:
            numeric = series.cast(pl.Float64, strict=False)
        except Exception:
            continue
        non_null = numeric.drop_nulls()
        if non_null.len() < 5:
            continue
        median_val = non_null.median()
        if not isinstance(median_val, (int, float)):
            continue
        median = float(median_val)
        abs_dev = (non_null - median).abs()
        mad_val = abs_dev.median()
        if not isinstance(mad_val, (int, float)):
            continue
        mad = float(mad_val)
        if mad == 0:
            continue
        scale = 1.4826 * mad
        robust_z = (numeric - median).abs() / scale
        mask = robust_z > threshold
        flagged_idx = [i for i, v in enumerate(mask.to_list()) if v]
        for row_id in flagged_idx[: settings.anomaly_per_detector_limit]:
            raw_val = numeric[row_id]
            if raw_val is None:
                continue
            z = float(robust_z[row_id])
            score_norm = min(100.0, z / threshold * 50.0)
            drafts.append(
                AnomalyDraft(
                    file_id=file.id,
                    row_id=row_id,
                    detector="numeric_mad",
                    reason_plain=(
                        f"{col.name} = {float(raw_val):g} is far from the typical value "
                        f"{median:g} for this column."
                    ),
                    reason_technical=(
                        f"|x - median| / (1.4826 * MAD) = {z:.2f} > {threshold} "
                        f"(median={median:g}, MAD={mad:g})"
                    ),
                    score_normalized=score_norm,
                    score_raw=z,
                    source_code=(
                        f"{table}[abs({table}['{col.name}'] - {table}['{col.name}'].median()) "
                        f"/ (1.4826 * ({table}['{col.name}'] - {table}['{col.name}'].median())"
                        f".abs().median()) > {threshold}]"
                    ),
                )
            )
    return drafts


def _detect_rare_value(
    *, file: File, schema: FileSchema, frame: pl.DataFrame, settings: Settings
) -> list[AnomalyDraft]:
    drafts: list[AnomalyDraft] = []
    table = slugify_table_name(file.filename)
    rare_threshold = settings.anomaly_rare_threshold
    for col in schema.columns:
        if col.inferred_kind not in RARE_VALUE_KINDS:
            continue
        if col.name not in frame.columns:
            continue
        series = frame[col.name]
        total = series.len()
        if total == 0:
            continue
        values = series.to_list()
        freq_map: dict[object, int] = {}
        for val in values:
            if val is None:
                continue
            freq_map[val] = freq_map.get(val, 0) + 1
        added = 0
        for row_id, val in enumerate(values):
            if val is None:
                continue
            count = freq_map.get(val, 0)
            freq = count / total
            if freq >= rare_threshold:
                continue
            score_norm = max(0.0, min(100.0, 100.0 * (1.0 - freq / rare_threshold)))
            drafts.append(
                AnomalyDraft(
                    file_id=file.id,
                    row_id=row_id,
                    detector="rare_value",
                    reason_plain=(
                        f"{col.name} = {val!r} appears only {count}/{total} times "
                        f"({freq:.2%}), below the {rare_threshold:.0%} rare threshold."
                    ),
                    reason_technical=(
                        f"freq({col.name}={val!r}) = {count}/{total} = {freq:.4f} "
                        f"< {rare_threshold}"
                    ),
                    score_normalized=score_norm,
                    score_raw=freq,
                    source_code=(
                        f"{table}[{table}['{col.name}'].map({table}['{col.name}']"
                        f".value_counts(normalize=True)) < {rare_threshold}]"
                    ),
                )
            )
            added += 1
            if added >= settings.anomaly_per_detector_limit:
                break
    return drafts


def _detect_key_overlap(
    *,
    file: File,
    files_by_id: dict[str, File],
    links_repo: LinkRepository,
    settings: Settings,
    session_id: str,
) -> list[AnomalyDraft]:
    drafts: list[AnomalyDraft] = []
    all_links = links_repo.list_for_session(session_id)
    incoming = [
        link for link in all_links if link.file_b == file.id and link.direction == "many_to_one"
    ]
    confirmed_incoming = []
    for link in incoming:
        review = links_repo.latest_review(link.id)
        if review is not None and review.action == "confirm":
            confirmed_incoming.append(link)
    if not confirmed_incoming:
        return drafts

    b_frame = _load_frame(file)
    for link in confirmed_incoming:
        file_a = files_by_id.get(link.file_a)
        if file_a is None:
            continue
        if link.col_a not in _load_frame(file_a).columns:
            continue
        if link.col_b not in b_frame.columns:
            continue
        a_frame = _load_frame(file_a)
        b_values = set(b_frame[link.col_b].drop_nulls().cast(pl.String).to_list())
        a_col = a_frame[link.col_a]
        a_strings = a_col.cast(pl.String).to_list()
        table_a = slugify_table_name(file_a.filename)
        table_b = slugify_table_name(file.filename)
        added = 0
        for row_id, val in enumerate(a_strings):
            if val is None:
                continue
            if val in b_values:
                continue
            drafts.append(
                AnomalyDraft(
                    file_id=file_a.id,
                    row_id=row_id,
                    detector="key_overlap",
                    reason_plain=(
                        f"{file_a.filename}.{link.col_a} = {val!r} has no matching "
                        f"{file.filename}.{link.col_b}."
                    ),
                    reason_technical=(
                        f"orphan foreign key: {link.col_a}={val!r} not in "
                        f"{file.filename}.{link.col_b}"
                    ),
                    score_normalized=80.0,
                    score_raw=1.0,
                    source_code=(
                        f"{table_a}[~{table_a}['{link.col_a}'].isin({table_b}['{link.col_b}'])]"
                    ),
                )
            )
            added += 1
            if added >= settings.anomaly_per_detector_limit:
                break
    return drafts


def detect_anomalies(
    *,
    session_id: str,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    links_repo: LinkRepository,
) -> list[AnomalyDraft]:
    files = files_repo.list_for_session(session_id)
    files_by_id = {f.id: f for f in files}
    drafts: list[AnomalyDraft] = []
    for file in files:
        schema = schemas_repo.get(file.id, file.schema_version)
        if schema is None:
            continue
        frame = _load_frame(file)
        drafts.extend(_detect_numeric_mad(file=file, schema=schema, frame=frame, settings=settings))
        drafts.extend(_detect_rare_value(file=file, schema=schema, frame=frame, settings=settings))
        drafts.extend(
            _detect_key_overlap(
                file=file,
                files_by_id=files_by_id,
                links_repo=links_repo,
                settings=settings,
                session_id=session_id,
            )
        )
    return drafts


def persist_anomalies(
    *,
    anomalies_repo: AnomalyRepository,
    session_id: str,
    drafts: list[AnomalyDraft],
) -> list[Anomaly]:
    created: list[Anomaly] = []
    for draft in drafts:
        anomaly = Anomaly(
            id=new_id(),
            session_id=session_id,
            file_id=draft.file_id,
            row_id=draft.row_id,
            detector=draft.detector,
            reason_plain=draft.reason_plain,
            reason_technical=draft.reason_technical,
            score_normalized=draft.score_normalized,
            score_raw=draft.score_raw,
            source_code=draft.source_code,
            created_at=datetime.now(UTC),
        )
        created.append(anomalies_repo.create(anomaly))
    return created
