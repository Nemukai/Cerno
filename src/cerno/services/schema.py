from __future__ import annotations

import re
from dataclasses import dataclass

import polars as pl

from cerno.models import FileSchema, InferredKind, SchemaColumn

NULL_SENTINELS = {"", "n/a", "na", "null", "none", "-", "--", "?", "nil"}

INDIAN_NUMERIC_PATTERN = re.compile(r"^-?\d{1,2}(?:,\d{2})+(?:,\d{3})(?:\.\d+)?$")
STANDARD_NUMERIC_PATTERN = re.compile(r"^-?\d+(?:,\d{3})*(?:\.\d+)?$")
DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{2}/\d{2}/\d{4}$"),
    re.compile(r"^\d{2}-\d{2}-\d{4}$"),
    re.compile(r"^\d{1,2}\s+[A-Za-z]{3,}\s+\d{4}$"),
]
DATETIME_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(:\d{2})?"),
]
BOOL_TRUE = {"true", "t", "yes", "y", "1"}
BOOL_FALSE = {"false", "f", "no", "n", "0"}

MAX_CATEGORY_CARDINALITY = 20


@dataclass
class SchemaHint:
    name: str
    dtype: str
    inferred_kind: InferredKind
    confidence: float


def strip_null_sentinels(series: pl.Series) -> pl.Series:
    if series.dtype != pl.String:
        return series
    lower = series.str.strip_chars().str.to_lowercase()
    mask = lower.is_in(list(NULL_SENTINELS))
    return series.set(mask, None)


def detect_kind(series: pl.Series) -> SchemaHint:
    name = series.name
    cleaned = strip_null_sentinels(series)
    non_null = cleaned.drop_nulls()
    total = len(cleaned)
    n = len(non_null)

    if n == 0:
        return SchemaHint(name=name, dtype=str(cleaned.dtype), inferred_kind="string", confidence=0.0)

    if cleaned.dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64):
        unique = non_null.n_unique()
        if unique <= MAX_CATEGORY_CARDINALITY and n >= 10:
            return SchemaHint(name=name, dtype=str(cleaned.dtype), inferred_kind="category", confidence=0.9)
        return SchemaHint(name=name, dtype=str(cleaned.dtype), inferred_kind="int", confidence=0.95)

    if cleaned.dtype in (pl.Float32, pl.Float64):
        return SchemaHint(name=name, dtype=str(cleaned.dtype), inferred_kind="float", confidence=0.95)

    if cleaned.dtype in (pl.Date,):
        return SchemaHint(name=name, dtype=str(cleaned.dtype), inferred_kind="date", confidence=0.99)

    if cleaned.dtype in (pl.Datetime,):
        return SchemaHint(name=name, dtype=str(cleaned.dtype), inferred_kind="datetime", confidence=0.99)

    if cleaned.dtype == pl.Boolean:
        return SchemaHint(name=name, dtype=str(cleaned.dtype), inferred_kind="bool", confidence=0.99)

    if cleaned.dtype == pl.String:
        return _classify_string(name, non_null, total)

    return SchemaHint(name=name, dtype=str(cleaned.dtype), inferred_kind="string", confidence=0.5)


def _classify_string(name: str, non_null: pl.Series, total: int) -> SchemaHint:
    samples = non_null.head(200).to_list()
    if not samples:
        return SchemaHint(name=name, dtype="String", inferred_kind="string", confidence=0.3)

    bool_hits = sum(1 for v in samples if v.strip().lower() in BOOL_TRUE | BOOL_FALSE)
    datetime_hits = sum(1 for v in samples if _matches_any(v, DATETIME_PATTERNS))
    date_hits = sum(1 for v in samples if _matches_any(v, DATE_PATTERNS))
    numeric_hits = sum(1 for v in samples if _looks_numeric(v))

    def ratio(hits: int) -> float:
        return hits / max(1, len(samples))

    if ratio(bool_hits) >= 0.9:
        return SchemaHint(name=name, dtype="String", inferred_kind="bool", confidence=ratio(bool_hits))
    if ratio(datetime_hits) >= 0.85:
        return SchemaHint(name=name, dtype="String", inferred_kind="datetime", confidence=ratio(datetime_hits))
    if ratio(date_hits) >= 0.85:
        return SchemaHint(name=name, dtype="String", inferred_kind="date", confidence=ratio(date_hits))
    if ratio(numeric_hits) >= 0.9:
        has_decimal = any("." in v for v in samples if _looks_numeric(v))
        kind: InferredKind = "float" if has_decimal else "int"
        return SchemaHint(name=name, dtype="String", inferred_kind=kind, confidence=ratio(numeric_hits))

    unique = non_null.n_unique()
    if total >= 10 and unique <= MAX_CATEGORY_CARDINALITY:
        return SchemaHint(name=name, dtype="String", inferred_kind="category", confidence=0.8)

    return SchemaHint(name=name, dtype="String", inferred_kind="string", confidence=0.7)


def _matches_any(value: str, patterns: list[re.Pattern[str]]) -> bool:
    stripped = value.strip()
    return any(p.match(stripped) for p in patterns)


def _looks_numeric(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    return bool(STANDARD_NUMERIC_PATTERN.match(stripped) or INDIAN_NUMERIC_PATTERN.match(stripped))


def build_schema(
    *, file_id: str, schema_version: int, frame: pl.DataFrame
) -> FileSchema:
    columns: list[SchemaColumn] = []
    for position, col_name in enumerate(frame.columns):
        hint = detect_kind(frame[col_name])
        columns.append(
            SchemaColumn(
                file_id=file_id,
                schema_version=schema_version,
                name=hint.name,
                dtype=hint.dtype,
                inferred_kind=hint.inferred_kind,
                confidence=hint.confidence,
                position=position,
            )
        )
    return FileSchema(file_id=file_id, schema_version=schema_version, columns=columns)


def needs_llm_verification(schema: FileSchema, threshold: float) -> list[SchemaColumn]:
    return [col for col in schema.columns if col.confidence < threshold]
