from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from pandas.api import types as pd_types

MAX_VALUE_LENGTH = 80
DEFAULT_TOP_K = 5
DEFAULT_SAMPLE_SIZE = 5
DEFAULT_OUTLIER_COUNT = 2
NUMERIC_PERCENTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


def profile_column(series: pd.Series) -> dict[str, Any]:
    values = series.dropna()
    total = len(series)
    non_null = len(values)
    display_values = [_display_value(value) for value in values]
    signatures = [_format_signature(value) for value in display_values]
    dominant_signature, signature_coverage = _dominant_signature(signatures)

    profile: dict[str, Any] = {
        "dtype": _canonical_dtype(series),
        "distinct_count": int(series.nunique(dropna=True)),
        "null_rate": _ratio(total - non_null, total),
        "top_values": _top_values(display_values, total=total),
        "signature": {
            "pattern": dominant_signature,
            "coverage": signature_coverage,
        },
        "samples": _diverse_samples(display_values),
        "outliers": _signature_outliers(display_values, signatures, dominant_signature),
    }
    numeric = _numeric_stats(series)
    if numeric is not None:
        profile["numeric"] = numeric
    return profile


def profile_table(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": [
            {
                "name": str(column),
                **profile_column(df[column]),
            }
            for column in df.columns
        ],
    }


def _canonical_dtype(series: pd.Series) -> str:
    dtype = series.dtype
    if pd_types.is_bool_dtype(dtype):
        return "bool"
    if pd_types.is_integer_dtype(dtype):
        return "int"
    if pd_types.is_float_dtype(dtype):
        return "float"
    if pd_types.is_datetime64_any_dtype(dtype):
        return "datetime"
    if isinstance(dtype, pd.CategoricalDtype):
        return "category"
    return "string"


def _numeric_stats(series: pd.Series) -> dict[str, Any] | None:
    if not pd_types.is_numeric_dtype(series.dtype) or pd_types.is_bool_dtype(series.dtype):
        return None
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    quantiles = numeric.quantile(NUMERIC_PERCENTILES)
    return {
        "min": _clean_number(numeric.min()),
        "max": _clean_number(numeric.max()),
        "mean": _clean_number(numeric.mean()),
        "std": _clean_number(numeric.std(ddof=0)),
        "p05": _clean_number(quantiles.loc[0.05]),
        "p25": _clean_number(quantiles.loc[0.25]),
        "p50": _clean_number(quantiles.loc[0.5]),
        "p75": _clean_number(quantiles.loc[0.75]),
        "p95": _clean_number(quantiles.loc[0.95]),
    }


def _top_values(values: Sequence[str], *, total: int) -> list[dict[str, Any]]:
    if total == 0:
        return []
    counts = Counter(values)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        {
            "value": value,
            "count": count,
            "rate": _ratio(count, total),
        }
        for value, count in ranked[:DEFAULT_TOP_K]
    ]


def _diverse_samples(values: Sequence[str]) -> list[str]:
    distinct = sorted(set(values), key=lambda value: (len(value), value))
    return _evenly_spaced(distinct, DEFAULT_SAMPLE_SIZE)


def _signature_outliers(
    values: Sequence[str], signatures: Sequence[str], dominant_signature: str
) -> list[str]:
    if not dominant_signature:
        return []
    distinct_outliers = sorted(
        {value for value, signature in zip(values, signatures, strict=True) if signature != dominant_signature},
        key=lambda value: (len(value), value),
    )
    return distinct_outliers[:DEFAULT_OUTLIER_COUNT]


def _dominant_signature(signatures: Sequence[str]) -> tuple[str, float]:
    if not signatures:
        return "", 0.0
    counts = Counter(signatures)
    signature, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    return signature, _ratio(count, len(signatures))


def _format_signature(value: str) -> str:
    if not value:
        return ""
    classes: list[str] = []
    for char in value:
        if char.isdigit():
            classes.append("D")
        elif char.isalpha() and char.isupper():
            classes.append("A")
        elif char.isalpha():
            classes.append("a")
        elif char.isspace():
            classes.append(" ")
        else:
            classes.append(char)
    return re.sub(r"([DAa ])\1+", _signature_run, "".join(classes))


def _signature_run(match: re.Match[str]) -> str:
    text = match.group(0)
    return f"{text[0]}{len(text)}"


def _display_value(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        if value.time() == pd.Timestamp(value.date()).time():
            rendered = value.date().isoformat()
        else:
            rendered = value.isoformat()
    elif isinstance(value, np.datetime64):
        rendered = pd.Timestamp(value).isoformat()
    elif isinstance(value, float):
        rendered = str(_clean_number(value))
    else:
        rendered = str(value)
    rendered = " ".join(rendered.split())
    if len(rendered) <= MAX_VALUE_LENGTH:
        return rendered
    return f"{rendered[: MAX_VALUE_LENGTH - 3]}..."


def _evenly_spaced(values: Sequence[str], limit: int) -> list[str]:
    if len(values) <= limit:
        return list(values)
    indexes = np.linspace(0, len(values) - 1, num=limit, dtype=int)
    return [values[int(index)] for index in indexes]


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _clean_number(value: Any) -> int | float:
    number = float(value)
    if not math.isfinite(number):
        return 0.0
    if number.is_integer():
        return int(number)
    return float(f"{number:.6g}")
