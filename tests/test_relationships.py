from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cerno.services.relationships import (
    RelationshipColumn,
    RelationshipScoringConfig,
    build_relationship_column,
    score_relationship_candidates,
)


def _column(
    file_id: str,
    column_name: str,
    values: Sequence[Any],
    *,
    dtype: str = "string",
    file_name: str | None = None,
) -> RelationshipColumn:
    return build_relationship_column(
        file_id=file_id,
        file_name=file_name or file_id,
        column_name=column_name,
        dtype=dtype,
        values=list(values),
    )


def test_clean_foreign_key_is_detected() -> None:
    parent_ids = [f"C{index:03d}" for index in range(1, 7)]
    child_ids = parent_ids + parent_ids

    candidates = score_relationship_candidates(
        [
            _column("orders", "customer_id", child_ids, file_name="Orders"),
            _column("customers", "id", parent_ids, file_name="Customers"),
        ],
        RelationshipScoringConfig(),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.child_file_id == "orders"
    assert candidate.parent_file_id == "customers"
    assert candidate.direction == "many_to_one"
    assert candidate.containment == 1.0
    assert candidate.parent_uniqueness == 1.0


def test_coincidental_date_and_boolean_overlap_is_rejected() -> None:
    dates = [f"2024-01-{day:02d}" for day in range(1, 11)]

    candidates = score_relationship_candidates(
        [
            _column("sales", "sale_date", dates, dtype="date"),
            _column("targets", "target_date", dates, dtype="date"),
            _column("a", "is_active", [True, False, True, False], dtype="bool"),
            _column("b", "approved", [True, False, True, False], dtype="bool"),
        ],
        RelationshipScoringConfig(),
    )

    assert candidates == []


def test_rare_code_overlap_scores_above_common_value_overlap() -> None:
    rare_values = [f"R{index:03d}" for index in range(1, 11)]
    common_values = [str(index) for index in range(10)]
    config = RelationshipScoringConfig(
        score_threshold=0.0,
        low_cardinality_distinct=0,
        min_shared_distinct=5,
    )

    candidates = score_relationship_candidates(
        [
            _column("orders", "customer_code", rare_values + rare_values),
            _column("customers", "code", rare_values),
            _column("events", "status_id", common_values + common_values),
            _column("statuses", "id", common_values),
            _column("flags", "flag_id", common_values),
            _column("states", "state_id", common_values),
        ],
        config,
    )
    scores = {
        (candidate.child_file_id, candidate.parent_file_id): candidate.score
        for candidate in candidates
    }

    assert scores[("orders", "customers")] > scores[("events", "statuses")]


def test_direction_inference_uses_parent_uniqueness() -> None:
    product_ids = [f"P{index:03d}" for index in range(1, 8)]
    line_item_ids = product_ids + product_ids[:4]

    candidate = score_relationship_candidates(
        [
            _column("line_items", "product_id", line_item_ids),
            _column("products", "id", product_ids),
        ],
        RelationshipScoringConfig(),
    )[0]

    assert candidate.child_file_id == "line_items"
    assert candidate.parent_file_id == "products"
    assert candidate.direction == "many_to_one"


def test_type_incompatible_high_overlap_pair_is_rejected() -> None:
    values = list(range(1, 8))

    candidates = score_relationship_candidates(
        [
            _column("calendar", "fiscal_day", values, dtype="date"),
            _column("facts", "day_number", values, dtype="int"),
        ],
        RelationshipScoringConfig(),
    )

    assert candidates == []
