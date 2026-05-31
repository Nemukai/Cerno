from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import polars as pl

from cerno.services.canonicalize import cast_column_for_dtype
from cerno.services.ingest import _raw_to_frame, _read_csv_rows
from cerno.services.reingest import _cast_column_with_metadata


class IngestCanonicalizeTests(unittest.TestCase):
    def test_lakh_grouped_and_western_grouped_integers_parse_identically(self) -> None:
        frame = _raw_to_frame([["1,23,456"], ["123,456"]])

        self.assertEqual(frame["c0"].to_list(), [123456, 123456])
        self.assertEqual(frame.schema["c0"], pl.Int64)

    def test_parenthesized_negative_and_currency_values_parse(self) -> None:
        frame = _raw_to_frame([["(₹1,234.50)"], ["Rs 2,000.00"], ["$3,100.25"]])

        self.assertEqual(frame["c0"].to_list(), [-1234.5, 2000.0, 3100.25])
        self.assertEqual(frame.schema["c0"], pl.Float64)

    def test_mojibake_is_repaired_on_string_cells(self) -> None:
        frame = _raw_to_frame([["cafÃ©"], ["MÃ¼nchen"]])

        self.assertEqual(frame["c0"].to_list(), ["café", "München"])

    def test_semicolon_delimited_csv_uses_detected_dialect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            path.write_bytes(b"name;amount\nalpha;1\nbeta;2\n")

            rows = _read_csv_rows(path)

        self.assertEqual(rows, [["name", "amount"], ["alpha", "1"], ["beta", "2"]])

    def test_dd_mm_date_column_resolution(self) -> None:
        column = cast_column_for_dtype(["13/02/2024", "14/02/2024"], "date")

        self.assertFalse(column.low_confidence)
        self.assertEqual(column.kind, "datetime")
        self.assertEqual(column.values, ["2024-02-13T00:00:00", "2024-02-14T00:00:00"])

    def test_ambiguous_date_column_is_flagged_not_guessed(self) -> None:
        column = cast_column_for_dtype(["01/02/2024", "03/04/2024"], "date")

        self.assertTrue(column.low_confidence)
        self.assertEqual(column.kind, "string")
        self.assertEqual(column.confidence, 0.5)
        self.assertEqual(column.values, ["01/02/2024", "03/04/2024"])

        casted = _cast_column_with_metadata(
            pl.Series("maybe_date", ["01/02/2024", "03/04/2024"]),
            "date",
        )
        self.assertEqual(casted.inferred_kind, "string")
        self.assertEqual(casted.confidence, 0.5)
        self.assertEqual(casted.series.to_list(), ["01/02/2024", "03/04/2024"])


if __name__ == "__main__":
    unittest.main()
