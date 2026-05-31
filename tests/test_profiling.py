from __future__ import annotations

import pandas as pd

from cerno.services.profiling import profile_column


def test_profile_column_numeric_stats() -> None:
    profile = profile_column(pd.Series([10, 20, 30, None, 100], dtype="float64"))

    assert profile["dtype"] == "float"
    assert profile["distinct_count"] == 4
    assert profile["null_rate"] == 0.2
    assert profile["numeric"]["min"] == 10
    assert profile["numeric"]["max"] == 100
    assert profile["numeric"]["p50"] == 25


def test_profile_column_categorical_top_values_and_signature() -> None:
    profile = profile_column(pd.Series(["open", "closed", "open", "pending", "open"], dtype="category"))

    assert profile["dtype"] == "category"
    assert profile["top_values"][0] == {"value": "open", "count": 3, "rate": 0.6}
    assert profile["signature"]["pattern"] == "a4"
    assert profile["outliers"] == ["closed", "pending"]


def test_profile_column_date_signature_and_samples() -> None:
    profile = profile_column(pd.Series(pd.to_datetime(["2024-02-13", "2024-02-14", None])))

    assert profile["dtype"] == "datetime"
    assert profile["signature"] == {"pattern": "D4-D2-D2", "coverage": 1.0}
    assert profile["samples"] == ["2024-02-13", "2024-02-14"]


def test_profile_column_high_null_rate() -> None:
    profile = profile_column(pd.Series([None, None, None, "A"]))

    assert profile["dtype"] == "string"
    assert profile["null_rate"] == 0.75
    assert profile["distinct_count"] == 1
    assert profile["samples"] == ["A"]


def test_profile_column_high_cardinality_samples_are_diverse() -> None:
    values = [f"id-{index:03d}" for index in range(50)]
    profile = profile_column(pd.Series(values))

    assert profile["dtype"] == "string"
    assert profile["distinct_count"] == 50
    assert len(profile["samples"]) == 5
    assert profile["samples"] != values[:5]
    assert profile["signature"] == {"pattern": "a2-D3", "coverage": 1.0}
