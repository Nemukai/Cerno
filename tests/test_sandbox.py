from __future__ import annotations

import unittest

import pandas as pd

from cerno.services.sandbox import run_python


class SandboxTests(unittest.TestCase):
    def test_import_statements_remain_blocked(self) -> None:
        result = run_python("import os\n1")

        self.assertFalse(result.ok)
        self.assertIn("imports are not allowed", result.error or "")

    def test_pandas_internal_imports_still_work(self) -> None:
        result = run_python(
            "orders['SHIFT'].value_counts(dropna=False).head(10)",
            tables={"orders": pd.DataFrame({"SHIFT": ["1", "2", "1"]})},
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.result_kind, "series")
        self.assertEqual(result.result_preview["values"], [2, 1])


if __name__ == "__main__":
    unittest.main()
