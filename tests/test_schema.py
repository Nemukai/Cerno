from __future__ import annotations

import polars as pl

from cerno.services.schema import (
    build_schema,
    detect_kind,
    needs_llm_verification,
    strip_null_sentinels,
)


def test_strip_null_sentinels() -> None:
    s = pl.Series("x", ["1", "N/A", "2", "-", "null", "3"])
    cleaned = strip_null_sentinels(s)
    assert cleaned.to_list() == ["1", None, "2", None, None, "3"]


def test_detect_int_column() -> None:
    s = pl.Series("age", [22, 34, 41, 29, 31, 44, 52, 27, 39, 36, 48, 30, 33, 45, 28, 40, 37, 42, 26, 35, 50, 32])
    hint = detect_kind(s)
    assert hint.inferred_kind == "int"
    assert hint.confidence >= 0.9


def test_detect_category_from_low_cardinality_int() -> None:
    s = pl.Series("status_code", [1, 2, 3, 1, 2, 1, 3, 2, 1, 1, 2, 3])
    hint = detect_kind(s)
    assert hint.inferred_kind == "category"


def test_detect_float_column() -> None:
    s = pl.Series("amount", [1.5, 2.7, 3.14, 99.99, 0.5])
    hint = detect_kind(s)
    assert hint.inferred_kind == "float"


def test_detect_date_string() -> None:
    s = pl.Series("dob", ["1990-01-01", "1985-05-23", "2001-12-31", "1975-07-04"])
    hint = detect_kind(s)
    assert hint.inferred_kind == "date"
    assert hint.confidence >= 0.85


def test_detect_indian_numeric_as_int_string() -> None:
    s = pl.Series("amount", ["1,23,456", "4,56,789", "12,34,567", "99,999"])
    hint = detect_kind(s)
    assert hint.inferred_kind == "int"


def test_detect_bool_string() -> None:
    s = pl.Series("active", ["Yes", "No", "yes", "no", "Y", "N"])
    hint = detect_kind(s)
    assert hint.inferred_kind == "bool"


def test_detect_category_string() -> None:
    s = pl.Series(
        "region",
        ["north", "south", "east", "west", "north", "south", "east", "west", "north", "south", "east", "west"],
    )
    hint = detect_kind(s)
    assert hint.inferred_kind == "category"


def test_detect_string_fallback() -> None:
    s = pl.Series("name", [f"Person {i}" for i in range(50)])
    hint = detect_kind(s)
    assert hint.inferred_kind == "string"


def test_build_schema_positions() -> None:
    frame = pl.DataFrame(
        {
            "id": list(range(100)),
            "name": [f"n{i}" for i in range(100)],
            "active": ["Y"] * 50 + ["N"] * 50,
        }
    )
    schema = build_schema(file_id="f1", schema_version=1, frame=frame)
    assert [c.name for c in schema.columns] == ["id", "name", "active"]
    assert [c.position for c in schema.columns] == [0, 1, 2]
    assert schema.columns[0].inferred_kind == "int"
    assert schema.columns[2].inferred_kind == "bool"


def test_needs_llm_verification() -> None:
    frame = pl.DataFrame({"weird": ["x-1", "x-2", "x-3"] * 3})
    schema = build_schema(file_id="f1", schema_version=1, frame=frame)
    low_conf = needs_llm_verification(schema, threshold=0.85)
    assert any(c.name == "weird" for c in low_conf)


def test_empty_column_does_not_crash() -> None:
    s = pl.Series("empty", [None, None, None], dtype=pl.String)
    hint = detect_kind(s)
    assert hint.inferred_kind == "string"
    assert hint.confidence == 0.0
