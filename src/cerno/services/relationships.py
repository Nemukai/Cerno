from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

import pandas as pd

from cerno.models import LinkDirection
from cerno.services.profiling import profile_column

RelationshipDType = Literal["string", "int", "float", "date", "datetime", "bool", "category"]

NUMERIC_DTYPES = {"int", "float"}
TEXT_DTYPES = {"string", "category"}
NULL_STRINGS = {"", "nan", "none", "null"}


@dataclass(frozen=True)
class RelationshipScoringConfig:
    containment_threshold: float = 0.85
    parent_uniqueness_threshold: float = 0.95
    score_threshold: float = 0.72
    min_distinct: int = 3
    min_shared_distinct: int = 5
    low_cardinality_distinct: int = 12
    max_avg_length: int = 100
    max_candidates: int = 30


@dataclass(frozen=True)
class RelationshipColumn:
    file_id: str
    file_name: str
    column_name: str
    dtype: RelationshipDType
    values: frozenset[str]
    counts: Counter[str]
    non_null_count: int
    signature: str
    signature_coverage: float

    @property
    def distinct_count(self) -> int:
        return len(self.values)

    @property
    def uniqueness(self) -> float:
        if self.non_null_count == 0:
            return 0.0
        return self.distinct_count / self.non_null_count

    @property
    def avg_length(self) -> float:
        if not self.values:
            return 0.0
        return sum(len(value) for value in self.values) / len(self.values)


@dataclass(frozen=True)
class RelationshipCandidate:
    candidate_id: str
    child_file_id: str
    child_file_name: str
    child_column: str
    parent_file_id: str
    parent_file_name: str
    parent_column: str
    direction: LinkDirection
    score: float
    containment: float
    reverse_containment: float
    weighted_containment: float
    rarity_score: float
    parent_uniqueness: float
    child_uniqueness: float
    name_similarity: float
    shared_distinct: int
    child_distinct: int
    parent_distinct: int
    dtype: str
    signature: str
    sample_values: tuple[str, ...]
    summary: str

    def verifier_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "score": self.score,
            "direction": self.direction,
            "child": {
                "file_id": self.child_file_id,
                "file": self.child_file_name,
                "column": self.child_column,
                "dtype": self.dtype,
                "distinct": self.child_distinct,
                "uniqueness": self.child_uniqueness,
            },
            "parent": {
                "file_id": self.parent_file_id,
                "file": self.parent_file_name,
                "column": self.parent_column,
                "dtype": self.dtype,
                "distinct": self.parent_distinct,
                "uniqueness": self.parent_uniqueness,
            },
            "signals": {
                "containment": self.containment,
                "reverse_containment": self.reverse_containment,
                "weighted_containment": self.weighted_containment,
                "rarity_score": self.rarity_score,
                "name_similarity": self.name_similarity,
                "shared_distinct": self.shared_distinct,
                "signature": self.signature,
            },
            "sample_values": list(self.sample_values),
        }


def build_relationship_column(
    *,
    file_id: str,
    file_name: str,
    column_name: str,
    dtype: str,
    values: list[Any],
) -> RelationshipColumn:
    normalized = [_normalize_value(value) for value in values]
    non_null_values = [value for value in normalized if value is not None]
    counts: Counter[str] = Counter(non_null_values)
    profile = profile_column(pd.Series(values))
    signature = profile.get("signature", {})
    return RelationshipColumn(
        file_id=file_id,
        file_name=file_name,
        column_name=column_name,
        dtype=_normalize_dtype(dtype),
        values=frozenset(counts),
        counts=counts,
        non_null_count=sum(counts.values()),
        signature=str(signature.get("pattern") or ""),
        signature_coverage=float(signature.get("coverage") or 0.0),
    )


def score_relationship_candidates(
    columns: list[RelationshipColumn],
    config: RelationshipScoringConfig,
) -> list[RelationshipCandidate]:
    value_document_frequency = _value_document_frequency(columns)
    candidates: list[RelationshipCandidate] = []
    for left_index, left in enumerate(columns):
        for right in columns[left_index + 1 :]:
            if left.file_id == right.file_id:
                continue
            best = _best_candidate(left, right, value_document_frequency, len(columns), config)
            if best is not None:
                candidates.append(best)
    candidates.sort(
        key=lambda candidate: (
            candidate.score,
            candidate.parent_uniqueness,
            candidate.containment,
            candidate.name_similarity,
        ),
        reverse=True,
    )
    return candidates[: config.max_candidates]


def _best_candidate(
    left: RelationshipColumn,
    right: RelationshipColumn,
    value_document_frequency: dict[str, int],
    total_columns: int,
    config: RelationshipScoringConfig,
) -> RelationshipCandidate | None:
    if not _columns_compatible(left, right):
        return None
    left_to_right = _score_ordered_candidate(
        left, right, value_document_frequency, total_columns, config
    )
    right_to_left = _score_ordered_candidate(
        right, left, value_document_frequency, total_columns, config
    )
    options = [candidate for candidate in (left_to_right, right_to_left) if candidate is not None]
    if not options:
        return None
    return max(options, key=lambda candidate: candidate.score)


def _score_ordered_candidate(
    child: RelationshipColumn,
    parent: RelationshipColumn,
    value_document_frequency: dict[str, int],
    total_columns: int,
    config: RelationshipScoringConfig,
) -> RelationshipCandidate | None:
    if child.distinct_count < config.min_distinct or parent.distinct_count < config.min_distinct:
        return None
    if child.avg_length > config.max_avg_length or parent.avg_length > config.max_avg_length:
        return None
    shared_values = child.values & parent.values
    shared_distinct = len(shared_values)
    if shared_distinct < config.min_shared_distinct:
        return None

    containment = shared_distinct / child.distinct_count
    reverse_containment = shared_distinct / parent.distinct_count
    weighted_containment = _weighted_containment(
        child.values, shared_values, value_document_frequency, total_columns
    )
    if containment < config.containment_threshold and weighted_containment < config.containment_threshold:
        return None

    name_similarity = _name_similarity(child.column_name, parent.column_name)
    rarity_score = _rarity_score(shared_values, value_document_frequency, total_columns)
    parent_uniqueness = _round(parent.uniqueness)
    child_uniqueness = _round(child.uniqueness)
    direction = _direction_for(child, parent, containment, reverse_containment, config)
    if _looks_like_coincidental_temporal_overlap(child, parent, name_similarity, config):
        return None

    score = (
        containment * 0.4
        + weighted_containment * 0.22
        + min(parent.uniqueness, 1.0) * 0.18
        + rarity_score * 0.12
        + name_similarity * 0.08
    )
    if direction == "many_to_many":
        score *= 0.82
    if _is_low_cardinality(child, parent, config) and name_similarity < 0.65:
        score = min(score, 0.55)
    score = _round(min(score, 1.0))
    if score < config.score_threshold:
        return None

    samples = tuple(sorted(shared_values)[:10])
    candidate_id = _candidate_id(child, parent)
    summary = (
        f"{child.file_name}.{child.column_name} contains {shared_distinct} shared "
        f"distinct values from {parent.file_name}.{parent.column_name} "
        f"(containment {containment:.2f}, parent uniqueness {parent.uniqueness:.2f})."
    )
    return RelationshipCandidate(
        candidate_id=candidate_id,
        child_file_id=child.file_id,
        child_file_name=child.file_name,
        child_column=child.column_name,
        parent_file_id=parent.file_id,
        parent_file_name=parent.file_name,
        parent_column=parent.column_name,
        direction=direction,
        score=score,
        containment=_round(containment),
        reverse_containment=_round(reverse_containment),
        weighted_containment=_round(weighted_containment),
        rarity_score=_round(rarity_score),
        parent_uniqueness=parent_uniqueness,
        child_uniqueness=child_uniqueness,
        name_similarity=_round(name_similarity),
        shared_distinct=shared_distinct,
        child_distinct=child.distinct_count,
        parent_distinct=parent.distinct_count,
        dtype=_combined_dtype(child.dtype, parent.dtype),
        signature=child.signature if child.signature == parent.signature else "",
        sample_values=samples,
        summary=summary,
    )


def _direction_for(
    child: RelationshipColumn,
    parent: RelationshipColumn,
    containment: float,
    reverse_containment: float,
    config: RelationshipScoringConfig,
) -> LinkDirection:
    child_unique = child.uniqueness >= config.parent_uniqueness_threshold
    parent_unique = parent.uniqueness >= config.parent_uniqueness_threshold
    if child_unique and parent_unique and containment >= 0.95 and reverse_containment >= 0.95:
        return "one_to_one"
    if parent_unique:
        return "many_to_one"
    return "many_to_many"


def _columns_compatible(left: RelationshipColumn, right: RelationshipColumn) -> bool:
    if left.dtype == right.dtype:
        return _signatures_compatible(left, right)
    if left.dtype in NUMERIC_DTYPES and right.dtype in NUMERIC_DTYPES:
        return True
    if left.dtype in TEXT_DTYPES and right.dtype in TEXT_DTYPES:
        return _signatures_compatible(left, right)
    return False


def _signatures_compatible(left: RelationshipColumn, right: RelationshipColumn) -> bool:
    if not left.signature or not right.signature:
        return True
    if left.signature_coverage < 0.75 or right.signature_coverage < 0.75:
        return True
    return left.signature == right.signature


def _weighted_containment(
    child_values: frozenset[str],
    shared_values: frozenset[str],
    value_document_frequency: dict[str, int],
    total_columns: int,
) -> float:
    denominator = sum(_idf(value, value_document_frequency, total_columns) for value in child_values)
    if denominator == 0:
        return 0.0
    numerator = sum(_idf(value, value_document_frequency, total_columns) for value in shared_values)
    return numerator / denominator


def _rarity_score(
    values: frozenset[str],
    value_document_frequency: dict[str, int],
    total_columns: int,
) -> float:
    if not values or total_columns <= 1:
        return 0.0
    max_extra = math.log((total_columns + 1) / 2)
    if max_extra <= 0:
        return 0.0
    avg_extra = (
        sum(_idf(value, value_document_frequency, total_columns) - 1 for value in values)
        / len(values)
    )
    return min(max(avg_extra / max_extra, 0.0), 1.0)


def _idf(
    value: str,
    value_document_frequency: dict[str, int],
    total_columns: int,
) -> float:
    return math.log((total_columns + 1) / (value_document_frequency.get(value, 0) + 1)) + 1


def _value_document_frequency(columns: list[RelationshipColumn]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for column in columns:
        counts.update(column.values)
    return dict(counts)


def _name_similarity(left: str, right: str) -> float:
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if not left_norm or not right_norm:
        return 0.0
    ratio = SequenceMatcher(a=left_norm, b=right_norm).ratio()
    if left_norm == right_norm:
        ratio = 1.0
    if _looks_like_key_pair(left_norm, right_norm):
        ratio = max(ratio, 0.85)
    if "id" in {left_norm, right_norm} and (left_norm.endswith("id") or right_norm.endswith("id")):
        ratio = max(ratio, 0.75)
    return min(ratio, 1.0)


def _looks_like_key_pair(left: str, right: str) -> bool:
    key_terms = {"id", "code", "number", "no", "key"}
    if left == right:
        return True
    for term in key_terms:
        if right == term and left.endswith(term):
            return True
        if left == term and right.endswith(term):
            return True
    return False


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _is_low_cardinality(
    child: RelationshipColumn, parent: RelationshipColumn, config: RelationshipScoringConfig
) -> bool:
    return min(child.distinct_count, parent.distinct_count) < config.low_cardinality_distinct


def _looks_like_coincidental_temporal_overlap(
    child: RelationshipColumn,
    parent: RelationshipColumn,
    name_similarity: float,
    config: RelationshipScoringConfig,
) -> bool:
    if child.dtype not in {"date", "datetime"} or parent.dtype not in {"date", "datetime"}:
        return False
    child_unique = child.uniqueness >= config.parent_uniqueness_threshold
    parent_unique = parent.uniqueness >= config.parent_uniqueness_threshold
    return child_unique and parent_unique and name_similarity < 0.85


def _combined_dtype(left: RelationshipDType, right: RelationshipDType) -> str:
    if left == right:
        return left
    if left in NUMERIC_DTYPES and right in NUMERIC_DTYPES:
        return "numeric"
    return f"{left}/{right}"


def _candidate_id(child: RelationshipColumn, parent: RelationshipColumn) -> str:
    raw = "|".join(
        [
            child.file_id,
            child.column_name.lower(),
            parent.file_id,
            parent.column_name.lower(),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _normalize_dtype(dtype: str) -> RelationshipDType:
    normalized = dtype.lower().strip()
    if normalized in {"integer", "int64", "int32"}:
        return "int"
    if normalized in {"number", "numeric", "float64", "float32", "double"}:
        return "float"
    if normalized in {"timestamp"}:
        return "datetime"
    if normalized in {"boolean"}:
        return "bool"
    if normalized in {"string", "int", "float", "date", "datetime", "bool", "category"}:
        return normalized  # type: ignore[return-value]
    return "string"


def _normalize_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        text = value.isoformat()
    else:
        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass
        text = str(value)
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if normalized in NULL_STRINGS or len(normalized) > 100:
        return None
    return normalized


def _round(value: float) -> float:
    return round(value, 4)
