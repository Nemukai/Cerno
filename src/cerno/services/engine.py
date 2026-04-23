from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

if TYPE_CHECKING:
    import pandas as pd

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_READONLY_PREFIXES = {"select", "with", "show", "describe", "explain", "pragma"}


class EngineError(RuntimeError):
    pass


@dataclass
class SqlResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
        }


@dataclass
class TableInfo:
    name: str
    parquet_path: str
    row_count: int


class DuckDBEngine:
    """Registers session files as DuckDB views and exposes read-only SQL."""

    def __init__(self) -> None:
        self._conn = duckdb.connect(":memory:")
        self._tables: dict[str, TableInfo] = {}

    def register_file(
        self, *, table_name: str, parquet_path: Path | str, row_count: int
    ) -> None:
        if not _IDENT_RE.match(table_name):
            raise EngineError(f"invalid table name: {table_name}")
        path_str = str(parquet_path)
        escaped = path_str.replace("'", "''")
        self._conn.execute(
            f"CREATE OR REPLACE VIEW \"{table_name}\" "
            f"AS SELECT * FROM read_parquet('{escaped}')"
        )
        self._tables[table_name] = TableInfo(
            name=table_name, parquet_path=path_str, row_count=row_count
        )

    def list_tables(self) -> list[TableInfo]:
        return [self._tables[name] for name in sorted(self._tables)]

    def describe_table(self, table_name: str) -> list[dict[str, Any]]:
        self._require_table(table_name)
        rows = self._conn.execute(f'DESCRIBE "{table_name}"').fetchall()
        return [
            {"column": r[0], "type": r[1], "nullable": r[2] == "YES"} for r in rows
        ]

    def run_sql(self, sql: str, *, max_rows: int = 1000) -> SqlResult:
        if not _looks_readonly(sql):
            raise EngineError("only SELECT / WITH / SHOW / DESCRIBE queries are allowed")
        cursor = self._conn.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        fetched = cursor.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = [list(r) for r in fetched[:max_rows]]
        return SqlResult(
            columns=columns, rows=rows, row_count=len(rows), truncated=truncated
        )

    def to_pandas(self, table_name: str) -> pd.DataFrame:
        self._require_table(table_name)
        return self._conn.execute(f'SELECT * FROM "{table_name}"').fetchdf()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DuckDBEngine:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _require_table(self, table_name: str) -> None:
        if table_name not in self._tables:
            raise EngineError(f"unknown table: {table_name}")


def _looks_readonly(sql: str) -> bool:
    stripped = sql.strip().rstrip(";").lower()
    if not stripped:
        return False
    first = stripped.split(None, 1)[0]
    return first in _READONLY_PREFIXES
