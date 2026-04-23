from __future__ import annotations

import ast
import builtins
import io
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

_SAFE_BUILTIN_NAMES = (
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "callable",
    "chr",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "hasattr",
    "hash",
    "hex",
    "id",
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
    "object",
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
    "type",
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

SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES
}


class SandboxError(RuntimeError):
    pass


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


def _nan_to_none(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for v in values:
        if isinstance(v, float) and v != v:
            out.append(None)
        else:
            out.append(v)
    return out


def _preview_value(value: Any, *, max_rows: int = 20) -> tuple[str, dict[str, Any] | None]:
    if isinstance(value, pd.DataFrame):
        head = value.head(max_rows).astype(object)
        rows = [_nan_to_none(list(row)) for row in head.values.tolist()]
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
            "values": _nan_to_none(head_series.tolist()),
            "row_count": len(value),
            "truncated": len(value) > max_rows,
        }
    if isinstance(value, (int, float, str, bool)) or value is None:
        return "scalar", {"value": value}
    if isinstance(value, np.generic):
        return "scalar", {"value": value.item()}
    if isinstance(value, dict):
        return "dict", {"keys": list(map(str, value.keys()))[:max_rows]}
    if isinstance(value, (list, tuple, set)):
        seq = list(value)[:max_rows]
        return "sequence", {"values": seq, "length": len(value)}
    return "other", None


def run_python(
    code: str, *, tables: dict[str, pd.DataFrame] | None = None
) -> SandboxResult:
    tables = tables or {}
    try:
        body_src, tail_src = _split_last_expression(code)
    except SyntaxError as exc:
        return SandboxResult(
            ok=False,
            stdout="",
            stderr="",
            error=f"SyntaxError: {exc.msg}",
            traceback=traceback.format_exc(),
        )

    env: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "pd": pd,
        "np": np,
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


def _detect_tables_used(
    env: dict[str, Any], tables: dict[str, pd.DataFrame]
) -> list[str]:
    return [name for name in tables if env.get(name) is tables[name]]
