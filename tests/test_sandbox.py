from __future__ import annotations

import pandas as pd

from cerno.services.sandbox import run_python


def test_run_python_scalar_expression() -> None:
    result = run_python("1 + 2")
    assert result.ok
    assert result.result_kind == "scalar"
    assert result.result_preview == {"value": 3}


def test_run_python_with_table() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    result = run_python("customers['a'].sum()", tables={"customers": df})
    assert result.ok
    assert result.result_preview == {"value": 6}
    assert result.tables_used == ["customers"]


def test_run_python_dataframe_preview() -> None:
    df = pd.DataFrame({"x": range(50)})
    code = "big = pd.DataFrame({'x': range(50)})\nbig"
    result = run_python(code)
    assert result.ok
    assert result.result_kind == "dataframe"
    assert result.result_preview is not None
    assert result.result_preview["row_count"] == 50
    assert result.result_preview["truncated"] is True
    _ = df  # keep fixture import sensible


def test_print_captured() -> None:
    result = run_python("print('hello')")
    assert result.ok
    assert "hello" in result.stdout


def test_imports_blocked() -> None:
    result = run_python("import os\nos.listdir('/')")
    assert not result.ok
    assert "ImportError" in (result.error or "") or "NameError" in (result.error or "")


def test_error_captured() -> None:
    result = run_python("1 / 0")
    assert not result.ok
    assert "ZeroDivisionError" in (result.error or "")
    assert result.traceback is not None


def test_multiline_last_expression() -> None:
    code = "x = 10\ny = 20\nx + y"
    result = run_python(code)
    assert result.ok
    assert result.result_preview == {"value": 30}


def test_syntax_error() -> None:
    result = run_python("def broken(:\n    pass")
    assert not result.ok
    assert "SyntaxError" in (result.error or "")


def test_no_dunders_on_builtins() -> None:
    result = run_python("__import__('os')")
    assert not result.ok
