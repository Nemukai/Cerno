from __future__ import annotations

import ast
import builtins
import io
import math
import multiprocessing as mp
import os
import tempfile
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import date, datetime, time
from queue import Empty
from typing import Any

import numpy as np
import pandas as pd

try:
    import resource
except ImportError:  # pragma: no cover - Windows desktop build fallback.
    resource = None  # type: ignore[assignment]

_SAFE_BUILTIN_NAMES = (
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "chr",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "hex",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "oct",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "True",
    "False",
    "None",
    "Exception",
    "ValueError",
    "KeyError",
    "IndexError",
    "TypeError",
    "ZeroDivisionError",
    "ArithmeticError",
    "StopIteration",
)

SAFE_BUILTINS: dict[str, Any] = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MEMORY_BYTES = 1024 * 1024 * 1024

_BLOCKED_CALL_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "hasattr",
    "input",
    "locals",
    "memoryview",
    "open",
    "setattr",
    "super",
    "vars",
}
_BLOCKED_ATTRS = {
    "eval",
    "from_file",
    "fromfile",
    "fromregex",
    "genfromtxt",
    "get_handle",
    "load",
    "loads",
    "loadtxt",
    "memmap",
    "option_context",
    "pipe",
    "query",
    "read_clipboard",
    "read_csv",
    "read_excel",
    "read_feather",
    "read_fwf",
    "read_gbq",
    "read_hdf",
    "read_html",
    "read_json",
    "read_orc",
    "read_parquet",
    "read_pickle",
    "read_sas",
    "read_spss",
    "read_sql",
    "read_sql_query",
    "read_sql_table",
    "read_stata",
    "read_table",
    "read_xml",
    "save",
    "savetxt",
    "savez",
    "savez_compressed",
    "to_clipboard",
    "to_csv",
    "to_excel",
    "to_feather",
    "to_gbq",
    "to_hdf",
    "to_html",
    "to_json",
    "to_latex",
    "to_markdown",
    "to_orc",
    "to_parquet",
    "to_pickle",
    "to_sql",
    "to_stata",
    "to_xml",
}

_ALLOWED_PANDAS_ATTRS = frozenset(
    {
        "Categorical",
        "DataFrame",
        "Index",
        "Interval",
        "MultiIndex",
        "NA",
        "NaT",
        "Series",
        "Timestamp",
        "Timedelta",
        "array",
        "concat",
        "crosstab",
        "cut",
        "date_range",
        "isna",
        "isnull",
        "merge",
        "notna",
        "notnull",
        "pivot_table",
        "qcut",
        "to_datetime",
        "to_numeric",
        "to_timedelta",
        "unique",
    }
)

_ALLOWED_NUMPY_ATTRS = frozenset(
    {
        "abs",
        "arange",
        "array",
        "asarray",
        "ceil",
        "clip",
        "corrcoef",
        "e",
        "exp",
        "floor",
        "full",
        "histogram",
        "inf",
        "isclose",
        "isfinite",
        "isinf",
        "isnan",
        "linspace",
        "log",
        "log10",
        "max",
        "mean",
        "median",
        "min",
        "nan",
        "ones",
        "percentile",
        "pi",
        "polyfit",
        "quantile",
        "round",
        "select",
        "sqrt",
        "std",
        "sum",
        "var",
        "where",
        "zeros",
    }
)


class SandboxError(RuntimeError):
    pass


class _SafeModule:
    __slots__ = ("_allowed", "_module", "_name")

    def __init__(self, name: str, module: Any, allowed: frozenset[str]) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_allowed", allowed)

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("_") or attr not in self._allowed:
            raise SandboxError(f"{self._name} attribute is not available: {attr}")
        return getattr(self._module, attr)


@dataclass
class SandboxResult:
    ok: bool
    stdout: str
    stderr: str
    result_repr: str | None = None
    result_kind: str | None = None
    result_preview: dict[str, Any] | None = None
    error: str | None = None
    traceback: str | None = None
    tables_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "result_repr": self.result_repr,
            "result_kind": self.result_kind,
            "result_preview": self.result_preview,
            "error": self.error,
            "traceback": self.traceback,
            "tables_used": self.tables_used,
        }


def _split_last_expression(code: str) -> tuple[str, str | None]:
    """Return (exec_body, eval_tail) so we can capture the last expression value."""
    tree = ast.parse(code, mode="exec")
    if not tree.body:
        return code, None
    last = tree.body[-1]
    if isinstance(last, ast.Expr):
        body_nodes = tree.body[:-1]
        body_src = ast.unparse(ast.Module(body=body_nodes, type_ignores=[]))
        tail_src = ast.unparse(last.value)
        return body_src, tail_src
    return code, None


def _validate_ast(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise SandboxError("imports are not allowed")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise SandboxError("global and nonlocal statements are not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise SandboxError("dunder names are not allowed")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise SandboxError(f"private attribute is not allowed: {node.attr}")
            if node.attr in _BLOCKED_ATTRS:
                raise SandboxError(f"attribute is not allowed: {node.attr}")
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in _BLOCKED_CALL_NAMES:
                raise SandboxError(f"call is not allowed: {name}")


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _json_safe_list(values: list[Any]) -> list[Any]:
    return [_json_safe(value) for value in values]


def _preview_value(value: Any, *, max_rows: int = 20) -> tuple[str, dict[str, Any] | None]:
    if isinstance(value, pd.DataFrame):
        head = value.head(max_rows).astype(object)
        rows = [_json_safe_list(list(row)) for row in head.values.tolist()]
        return "dataframe", {
            "columns": [str(c) for c in head.columns],
            "rows": rows,
            "row_count": len(value),
            "truncated": len(value) > max_rows,
        }
    if isinstance(value, pd.Series):
        head_series: pd.Series = value.head(max_rows).astype(object)
        return "series", {
            "name": str(head_series.name) if head_series.name is not None else None,
            "values": _json_safe_list(head_series.tolist()),
            "row_count": len(value),
            "truncated": len(value) > max_rows,
        }
    if isinstance(value, (int, float, str, bool)) or value is None:
        return "scalar", {"value": _json_safe(value)}
    if isinstance(value, np.generic):
        return "scalar", {"value": _json_safe(value)}
    if isinstance(value, dict):
        items = list(value.items())[:max_rows]
        return "dict", {
            "items": [{"key": str(k), "value": _json_safe(v)} for k, v in items],
            "truncated": len(value) > max_rows,
        }
    if isinstance(value, (list, tuple, set)):
        seq = list(value)[:max_rows]
        return "sequence", {"values": _json_safe_list(seq), "length": len(value)}
    return "other", None


def run_python(
    code: str,
    *,
    tables: dict[str, pd.DataFrame] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    memory_bytes: int = DEFAULT_MEMORY_BYTES,
) -> SandboxResult:
    tables = tables or {}
    try:
        tree = ast.parse(code, mode="exec")
        _validate_ast(tree)
        body_src, tail_src = _split_last_expression(code)
    except SyntaxError as exc:
        return SandboxResult(
            ok=False,
            stdout="",
            stderr="",
            error=f"SyntaxError: {exc.msg}",
            traceback=traceback.format_exc(),
        )
    except SandboxError as exc:
        return SandboxResult(
            ok=False,
            stdout="",
            stderr="",
            error=f"SandboxError: {exc}",
            traceback=traceback.format_exc(),
        )

    ctx = mp.get_context("spawn")
    queue: mp.Queue[SandboxResult] = ctx.Queue(maxsize=1)
    proc = ctx.Process(
        target=_run_python_child,
        args=(body_src, tail_src, tables, queue, memory_bytes),
    )
    proc.start()
    proc.join(timeout_seconds)
    if proc.is_alive():
        proc.terminate()
        proc.join(1)
        if proc.is_alive():
            proc.kill()
            proc.join()
        return SandboxResult(
            ok=False,
            stdout="",
            stderr="",
            error=f"TimeoutError: code exceeded {timeout_seconds:g}s limit",
        )
    try:
        return queue.get_nowait()
    except Empty:
        return SandboxResult(
            ok=False,
            stdout="",
            stderr="",
            error=f"SandboxError: worker exited with code {proc.exitcode}",
        )


def _run_python_child(
    body_src: str,
    tail_src: str | None,
    tables: dict[str, pd.DataFrame],
    queue: mp.Queue[SandboxResult],
    memory_bytes: int,
) -> None:
    if resource is not None:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (OSError, ValueError):
            pass
    with tempfile.TemporaryDirectory(prefix="cerno-sandbox-") as td:
        os.chdir(td)
        result = _run_python_in_process(body_src, tail_src, tables)
    queue.put(result)


def _run_python_in_process(
    body_src: str, tail_src: str | None, tables: dict[str, pd.DataFrame]
) -> SandboxResult:

    env: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "pd": _SafeModule("pandas", pd, _ALLOWED_PANDAS_ATTRS),
        "np": _SafeModule("numpy", np, _ALLOWED_NUMPY_ATTRS),
    }
    env.update(tables)

    stdout = io.StringIO()
    stderr = io.StringIO()
    result: Any = None
    result_repr: str | None = None
    result_kind: str | None = None
    result_preview: dict[str, Any] | None = None

    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            if body_src.strip():
                exec(compile(body_src, "<cell>", "exec"), env)
            if tail_src is not None:
                result = eval(compile(tail_src, "<cell>", "eval"), env)
    except Exception as exc:
        return SandboxResult(
            ok=False,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
            tables_used=_detect_tables_used(env, tables),
        )

    if result is not None:
        result_kind, result_preview = _preview_value(result)
        try:
            result_repr = repr(result)[:2000]
        except Exception:
            result_repr = None

    return SandboxResult(
        ok=True,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        result_repr=result_repr,
        result_kind=result_kind,
        result_preview=result_preview,
        tables_used=_detect_tables_used(env, tables),
    )


def _detect_tables_used(env: dict[str, Any], tables: dict[str, pd.DataFrame]) -> list[str]:
    return [name for name in tables if env.get(name) is tables[name]]
