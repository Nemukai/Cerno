from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import dateparser  # type: ignore[import-untyped]
import pandas as pd
import polars as pl
from ftfy import fix_text

CanonicalKind = Literal["string", "int", "float", "datetime"]

NULL_TOKENS = {
    "",
    "#N/A",
    "#N/A N/A",
    "#NA",
    "#REF!",
    "#VALUE!",
    "-",
    "-1.#IND",
    "-1.#QNAN",
    "-NaN",
    "-nan",
    "1.#IND",
    "1.#QNAN",
    "<NA>",
    "N/A",
    "NA",
    "NULL",
    "NaN",
    "None",
    "na",
    "n/a",
    "nan",
    "nil",
    "NIL",
    "null",
    "\u2014",
    "\u2013",
}

_CURRENCY_RE = re.compile(
    r"(?i)(₹|rs\.?|inr|usd|us\$|\$|eur|€|gbp|£|aud|cad|sgd|aed|¥)"
)
_PARENS_NEGATIVE_RE = re.compile(r"^\((.*)\)$")
_DIGITS_RE = re.compile(r"\d")
_DECIMAL_COMMA_RE = re.compile(r"^[+-]?\d+,\d{1,2}$")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d.%m.%Y",
    "%m.%d.%Y",
    "%d/%m/%y",
    "%m/%d/%y",
    "%d-%m-%y",
    "%m-%d-%y",
    "%d.%m.%y",
    "%m.%d.%y",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%m-%d-%Y %H:%M:%S",
)


@dataclass(frozen=True)
class CanonicalColumn:
    values: list[Any]
    kind: CanonicalKind
    confidence: float = 1.0
    low_confidence: bool = False
    reason: str | None = None


def normalize_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        repaired = fix_text(value).strip()
        return None if repaired in NULL_TOKENS else repaired
    return value


def canonicalize_inferred_column(values: list[Any]) -> CanonicalColumn:
    normalized = [normalize_cell(value) for value in values]
    first = next((value for value in normalized if value is not None), None)
    if first is None:
        return CanonicalColumn(values=normalized, kind="string")

    numeric = _coerce_numeric_column(normalized, require_first_value=True)
    if numeric is not None:
        return numeric

    dates = _coerce_date_column(normalized, require_first_value=True)
    if dates is not None and not dates.low_confidence:
        return dates

    return CanonicalColumn(values=normalized, kind="string")


def cast_column_for_dtype(values: list[Any], dtype: str) -> CanonicalColumn:
    normalized = [normalize_cell(value) for value in values]
    target = dtype.strip().lower()
    if target in {"int", "integer", "float", "number", "numeric"}:
        numeric = _coerce_numeric_column(normalized, require_first_value=False)
        if numeric is None:
            return CanonicalColumn(
                values=[None] * len(normalized),
                kind="float" if target != "int" else "int",
            )
        if target == "float" and numeric.kind == "int":
            return CanonicalColumn(
                values=[float(value) if value is not None else None for value in numeric.values],
                kind="float",
                confidence=numeric.confidence,
            )
        if target in {"int", "integer"} and numeric.kind == "float":
            coerced = [
                int(value) if isinstance(value, float | int) and float(value).is_integer() else None
                for value in numeric.values
            ]
            return CanonicalColumn(values=coerced, kind="int", confidence=numeric.confidence)
        return numeric
    if target in {"date", "datetime"}:
        dates = _coerce_date_column(normalized, require_first_value=False)
        if dates is None:
            return CanonicalColumn(values=normalized, kind="string", confidence=0.5, low_confidence=True)
        return dates
    return CanonicalColumn(values=normalized, kind="string")


def to_polars_series(name: str, column: CanonicalColumn) -> pl.Series:
    if column.kind == "int":
        return pl.Series(name, column.values, dtype=pl.Int64)
    if column.kind == "float":
        return pl.Series(name, column.values, dtype=pl.Float64)
    return pl.Series(name, column.values)


def iso_to_datetime_series(name: str, column: CanonicalColumn, *, as_date: bool) -> pl.Series:
    series = pl.Series(name, column.values, dtype=pl.String)
    parsed = series.str.to_datetime(strict=False)
    return parsed.dt.date() if as_date else parsed


def _coerce_numeric_column(
    values: list[Any], *, require_first_value: bool
) -> CanonicalColumn | None:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return None
    if require_first_value and _parse_decimal(non_null[0], decimal_comma=False) is None:
        return None

    decimal_comma = _uses_decimal_comma(non_null)
    parsed: list[Decimal | None] = [
        _parse_decimal(value, decimal_comma=decimal_comma) for value in values
    ]
    parsed_count = sum(value is not None for value in parsed)
    confidence = parsed_count / len(non_null)
    threshold = 0.85 if require_first_value else 0.0
    if parsed_count == 0 or confidence < threshold:
        return None

    integral = all(value is None or value == value.to_integral_value() for value in parsed)
    if integral:
        return CanonicalColumn(
            values=[int(value) if value is not None else None for value in parsed],
            kind="int",
            confidence=confidence,
        )
    return CanonicalColumn(
        values=[float(value) if value is not None else None for value in parsed],
        kind="float",
        confidence=confidence,
    )


def _parse_decimal(value: Any, *, decimal_comma: bool) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = normalize_cell(value)
    if not isinstance(text, str) or not _DIGITS_RE.search(text):
        return None

    negative = False
    match = _PARENS_NEGATIVE_RE.match(text)
    if match:
        negative = True
        text = match.group(1).strip()

    text = _CURRENCY_RE.sub("", text)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = text.replace(" ", "")
    if decimal_comma:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    text = text.strip()
    if negative and not text.startswith("-"):
        text = f"-{text}"
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _uses_decimal_comma(values: list[Any]) -> bool:
    comma_values: list[str] = []
    for value in values:
        normalized = normalize_cell(value)
        if not isinstance(normalized, str):
            continue
        text = _CURRENCY_RE.sub("", normalized).strip()
        if "," not in text or "." in text:
            continue
        comma_values.append(text.replace(" ", ""))
    if not comma_values:
        return False
    return all(_DECIMAL_COMMA_RE.match(text) is not None for text in comma_values)


def _coerce_date_column(values: list[Any], *, require_first_value: bool) -> CanonicalColumn | None:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return None
    string_values = [str(value) for value in non_null]
    if require_first_value and _parse_with_formats(str(non_null[0])) is None:
        return None

    best = _best_strptime_format(string_values)
    if best is None:
        fallback = _dateparser_column(values)
        return fallback
    fmt, parsed_count, second_count, ambiguous = best
    confidence = parsed_count / len(non_null)
    if ambiguous or parsed_count == second_count:
        return CanonicalColumn(
            values=[normalize_cell(value) for value in values],
            kind="string",
            confidence=0.5,
            low_confidence=True,
            reason="ambiguous_date_format",
        )
    if confidence < 0.85:
        return None

    parsed_values = pandas_datetime_with_format([normalize_cell(value) for value in values], fmt)
    return CanonicalColumn(values=parsed_values, kind="datetime", confidence=confidence)


def _best_strptime_format(values: list[str]) -> tuple[str, int, int, bool] | None:
    scores: list[tuple[str, int]] = []
    for fmt in _DATE_FORMATS:
        count = 0
        for value in values:
            try:
                datetime.strptime(value, fmt)
                count += 1
            except ValueError:
                pass
        if count:
            scores.append((fmt, count))
    if not scores:
        return None
    scores.sort(key=lambda item: item[1], reverse=True)
    best_fmt, best_count = scores[0]
    second_count = scores[1][1] if len(scores) > 1 else 0
    ambiguous = _is_day_month_ambiguous(values, best_count, second_count)
    return best_fmt, best_count, second_count, ambiguous


def _is_day_month_ambiguous(values: list[str], best_count: int, second_count: int) -> bool:
    if best_count != second_count:
        return False
    slash_dates = [
        value
        for value in values
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}:\d{2})?$", value)
    ]
    if not slash_dates:
        return False
    return all(_both_day_month_orders_parse(value) for value in slash_dates)


def _both_day_month_orders_parse(value: str) -> bool:
    formats = ("%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y", "%m/%d/%y")
    parsed = 0
    for fmt in formats:
        try:
            datetime.strptime(value, fmt)
            parsed += 1
        except ValueError:
            pass
    return parsed >= 2


def _parse_with_formats(value: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def _dateparser_column(values: list[Any]) -> CanonicalColumn | None:
    parsed_values: list[str | None] = []
    non_null_count = 0
    parsed_count = 0
    for value in values:
        normalized = normalize_cell(value)
        if normalized is None:
            parsed_values.append(None)
            continue
        non_null_count += 1
        parsed = dateparser.parse(
            str(normalized),
            settings={"STRICT_PARSING": True, "RETURN_AS_TIMEZONE_AWARE": False},
        )
        if parsed is None:
            parsed_values.append(None)
            continue
        parsed_count += 1
        parsed_values.append(parsed.isoformat())
    if non_null_count == 0:
        return None
    confidence = parsed_count / non_null_count
    if confidence < 0.85:
        return None
    return CanonicalColumn(values=parsed_values, kind="datetime", confidence=confidence)


def pandas_datetime_with_format(values: list[Any], fmt: str) -> list[str | None]:
    parsed = pd.to_datetime(values, format=fmt, errors="coerce")
    return [None if pd.isna(value) else value.isoformat() for value in parsed]
