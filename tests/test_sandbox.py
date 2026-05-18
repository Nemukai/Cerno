from __future__ import annotations

import unittest

import pandas as pd

from cerno.services.sandbox import run_python


class SandboxTests(unittest.TestCase):
    def test_import_statements_remain_blocked(self) -> None:
        result = run_python("import os\n1")

        self.assertFalse(result.ok)
        self.assertIn("imports are not allowed", result.error or "")

    def test_dynamic_import_escape_is_blocked(self) -> None:
        result = run_python(
            'b=getattr(pd, "__builtins__")\n'
            'imp=b["__import__"]\n'
            'os=imp("os")\n'
            'os.listdir("/tmp")[:1]',
            tables={"orders": pd.DataFrame({"SHIFT": ["1"]})},
        )

        self.assertFalse(result.ok)
        self.assertIn("call is not allowed: getattr", result.error or "")

    def test_dynamic_pandas_file_read_is_blocked(self) -> None:
        result = run_python(
            'getattr(pd, "read_csv")("/etc/hosts", header=None).head(1)',
            tables={"orders": pd.DataFrame({"SHIFT": ["1"]})},
        )

        self.assertFalse(result.ok)
        self.assertIn("call is not allowed: getattr", result.error or "")

    def test_direct_pandas_file_read_is_blocked(self) -> None:
        result = run_python(
            'pd.read_csv("/etc/hosts", header=None).head(1)',
            tables={"orders": pd.DataFrame({"SHIFT": ["1"]})},
        )

        self.assertFalse(result.ok)
        self.assertIn("attribute is not allowed: read_csv", result.error or "")

    def test_pandas_submodule_file_handle_escape_is_blocked(self) -> None:
        result = run_python(
            'pd.io.common.get_handle("/etc/hosts", "r")',
            tables={"orders": pd.DataFrame({"SHIFT": ["1"]})},
        )

        self.assertFalse(result.ok)
        self.assertIn("attribute is not allowed: get_handle", result.error or "")

    def test_pandas_internal_imports_still_work(self) -> None:
        result = run_python(
            "orders['SHIFT'].value_counts(dropna=False).head(10)",
            tables={"orders": pd.DataFrame({"SHIFT": ["1", "2", "1"]})},
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.result_kind, "series")
        self.assertEqual(result.result_preview["values"], [2, 1])

    def test_safe_pandas_and_numpy_helpers_still_work(self) -> None:
        result = run_python(
            "orders['when'] = pd.to_datetime(orders['when'])\n"
            "orders['bucket'] = np.where(orders['amount'] >= 20, 'high', 'low')\n"
            "orders.groupby('bucket')['amount'].sum().sort_index()",
            tables={
                "orders": pd.DataFrame(
                    {
                        "when": ["2026-01-01", "2026-01-02", "2026-01-03"],
                        "amount": [10, 20, 30],
                    }
                )
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.result_kind, "series")
        self.assertEqual(result.result_preview["values"], [50, 10])


if __name__ == "__main__":
    unittest.main()
