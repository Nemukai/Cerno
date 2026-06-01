from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from cerno.services.canonicalize import cast_column_for_dtype, normalize_cell
from cerno.services.profiling import profile_column

ValidatedDType = Literal["string", "int", "float", "date", "datetime", "bool", "category"]

TRUTHY = {"true", "t", "yes", "y", "1"}
FALSY = {"false", "f", "no", "n", "0"}
LOW_CONFIDENCE_DEFAULT = 0.5


@dataclass(frozen=True)
class ColumnValidation:
    confidence: float
    reasons: tuple[str, ...]
    profile: dict[str, Any]

    @property
    def low_confidence(self) -> bool:
        return bool(self.reasons)

    def encoded_reasons(self) -> str | None:
        if not self.reasons:
            return None
        return json.dumps(list(self.reasons), separators=(",", ":"))


@dataclass(frozen=True)
class FieldValidationInput:
    file_id: str
    file_name: str
    column_id: str
    name: str
    dtype: str
    description: str
    values: list[Any]
    sibling_names: tuple[str, ...] = ()


def validate_discovered_column(field: FieldValidationInput) -> ColumnValidation:
    values = [normalize_cell(value) for value in field.values]
    non_null = [value for value in values if value is not None]
    profile = profile_column(pd.Series(values))
    reasons: list[str] = []
    header_score = _header_score(field.name, field.sibling_names, reasons)
    dtype = _normalize_dtype(field.dtype)
    type_score = _type_score(dtype, values, non_null, profile, reasons)
    semantic_score = _semantic_score(dtype, field.name, profile, reasons)
    confidence = round(max(min((type_score * 0.65) + (semantic_score * 0.2) + (header_score * 0.15), 1.0), 0.0), 4)
    if confidence < LOW_CONFIDENCE_DEFAULT and "validator_confidence_low" not in reasons:
        reasons.append("validator_confidence_low")
    return ColumnValidation(confidence=confidence, reasons=tuple(dict.fromkeys(reasons)), profile=profile)


def decode_confidence_reasons(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def _header_score(name: str, sibling_names: tuple[str, ...], reasons: list[str]) -> float:
    stripped = name.strip()
    if not stripped:
        reasons.append("empty_column_name")
        return 0.0
    duplicates = sum(1 for sibling in sibling_names if sibling.strip().lower() == stripped.lower())
    score = 1.0
    if duplicates > 1:
        reasons.append("duplicate_column_name")
        score = min(score, 0.55)
    if re.fullmatch(r"c\d+|column\d+|unnamed:?\s*\d*", stripped.lower()):
        reasons.append("generic_column_name")
        score = min(score, 0.75)
    return score


def _type_score(
    dtype: ValidatedDType,
    values: list[Any],
    non_null: list[Any],
    profile: dict[str, Any],
    reasons: list[str],
) -> float:
    if not non_null:
        reasons.append("all_values_null")
        return 0.65
    if dtype in {"int", "float", "date", "datetime"}:
        canonical = cast_column_for_dtype(values, dtype)
        parse_rate = _parse_rate(values, canonical.values, len(non_null))
        if dtype in {"date", "datetime"} and canonical.kind != "datetime":
            parse_rate = 0.0
        if dtype == "int" and canonical.kind != "int":
            parse_rate = 0.0
        if dtype == "float" and canonical.kind not in {"int", "float"}:
            parse_rate = 0.0
        if canonical.low_confidence and canonical.reason:
            reasons.append(canonical.reason)
        if parse_rate < 0.85:
            reasons.append("type_parse_rate_low")
        if dtype in {"date", "datetime"} and canonical.kind != "datetime":
            reasons.append("type_kind_mismatch")
        if dtype == "int" and canonical.kind != "int":
            reasons.append("type_kind_mismatch")
        if dtype == "float" and canonical.kind not in {"int", "float"}:
            reasons.append("type_kind_mismatch")
        return min(parse_rate, canonical.confidence)
    if dtype == "bool":
        rate = _bool_parse_rate(non_null)
        if rate < 0.85:
            reasons.append("type_parse_rate_low")
        if len(set(map(str, non_null))) <= 2:
            return max(rate, 0.85)
        return rate
    if dtype == "category":
        distinct = int(profile["distinct_count"])
        rate = distinct / len(non_null)
        if distinct > 50 and rate > 0.5:
            reasons.append("high_cardinality_category")
            return 0.65
        return 0.95
    return 0.95


def _semantic_score(
    dtype: ValidatedDType, name: str, profile: dict[str, Any], reasons: list[str]
) -> float:
    signature = profile.get("signature", {})
    pattern = str(signature.get("pattern") or "")
    lower_name = name.lower()
    if dtype in {"date", "datetime"} and "D" not in pattern:
        reasons.append("format_signature_mismatch")
        return 0.6
    if dtype in {"int", "float"} and pattern and "D" not in pattern:
        reasons.append("format_signature_mismatch")
        return 0.6
    if dtype == "category" and any(term in lower_name for term in ("amount", "price", "total", "rate")):
        reasons.append("semantic_name_type_mismatch")
        return 0.75
    return 1.0


def _parse_rate(original_values: list[Any], parsed_values: list[Any], non_null_count: int) -> float:
    if non_null_count == 0:
        return 0.0
    parsed = sum(
        original is not None and parsed is not None
        for original, parsed in zip(original_values, parsed_values, strict=True)
    )
    return parsed / non_null_count


def _bool_parse_rate(values: list[Any]) -> float:
    if not values:
        return 0.0
    parsed = 0
    for value in values:
        if isinstance(value, bool):
            parsed += 1
            continue
        text = str(value).strip().lower()
        if text in TRUTHY or text in FALSY:
            parsed += 1
    return parsed / len(values)


def _normalize_dtype(dtype: str) -> ValidatedDType:
    normalized = dtype.strip().lower()
    if normalized in {"integer"}:
        return "int"
    if normalized in {"number", "numeric"}:
        return "float"
    if normalized in {"timestamp"}:
        return "datetime"
    if normalized in {"boolean"}:
        return "bool"
    if normalized in {"string", "int", "float", "date", "datetime", "bool", "category"}:
        return normalized  # type: ignore[return-value]
    return "string"
