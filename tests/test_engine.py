from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from cerno.services.engine import DuckDBEngine, EngineError


@pytest.fixture
def two_parquets(tmp_path: Path) -> tuple[Path, Path]:
    customers = pl.DataFrame(
        {"id": [1, 2, 3], "name": ["Ada", "Bea", "Cal"]}
    )
    orders = pl.DataFrame(
        {"order_id": [10, 11, 12, 13], "customer_id": [1, 1, 2, 3]}
    )
    p1 = tmp_path / "customers.parquet"
    p2 = tmp_path / "orders.parquet"
    customers.write_parquet(p1)
    orders.write_parquet(p2)
    return p1, p2


def test_register_and_list_tables(two_parquets: tuple[Path, Path]) -> None:
    p1, p2 = two_parquets
    with DuckDBEngine() as eng:
        eng.register_file(table_name="customers", parquet_path=p1, row_count=3)
        eng.register_file(table_name="orders", parquet_path=p2, row_count=4)
        names = [t.name for t in eng.list_tables()]
        assert names == ["customers", "orders"]


def test_describe_table(two_parquets: tuple[Path, Path]) -> None:
    p1, _ = two_parquets
    with DuckDBEngine() as eng:
        eng.register_file(table_name="customers", parquet_path=p1, row_count=3)
        described = eng.describe_table("customers")
        assert {c["column"] for c in described} == {"id", "name"}


def test_run_sql_select(two_parquets: tuple[Path, Path]) -> None:
    p1, p2 = two_parquets
    with DuckDBEngine() as eng:
        eng.register_file(table_name="customers", parquet_path=p1, row_count=3)
        eng.register_file(table_name="orders", parquet_path=p2, row_count=4)
        result = eng.run_sql(
            "SELECT c.name, COUNT(*) AS order_count "
            "FROM orders o JOIN customers c ON o.customer_id = c.id "
            "GROUP BY c.name ORDER BY c.name"
        )
        assert result.columns == ["name", "order_count"]
        assert result.row_count == 3
        assert result.rows[0][0] == "Ada"


def test_run_sql_rejects_mutations(two_parquets: tuple[Path, Path]) -> None:
    p1, _ = two_parquets
    with DuckDBEngine() as eng:
        eng.register_file(table_name="customers", parquet_path=p1, row_count=3)
        with pytest.raises(EngineError):
            eng.run_sql("DELETE FROM customers")
        with pytest.raises(EngineError):
            eng.run_sql("CREATE TABLE x AS SELECT 1")


def test_run_sql_truncates(two_parquets: tuple[Path, Path]) -> None:
    _, p2 = two_parquets
    with DuckDBEngine() as eng:
        eng.register_file(table_name="orders", parquet_path=p2, row_count=4)
        result = eng.run_sql("SELECT * FROM orders", max_rows=2)
        assert result.row_count == 2
        assert result.truncated is True


def test_register_rejects_bad_identifier(two_parquets: tuple[Path, Path]) -> None:
    p1, _ = two_parquets
    with DuckDBEngine() as eng:
        with pytest.raises(EngineError):
            eng.register_file(table_name="Bad Name", parquet_path=p1, row_count=3)
        with pytest.raises(EngineError):
            eng.register_file(table_name='"; DROP TABLE x; --', parquet_path=p1, row_count=3)


def test_to_pandas_unknown_table() -> None:
    with DuckDBEngine() as eng:
        with pytest.raises(EngineError):
            eng.to_pandas("missing")


def test_run_sql_with_cte(two_parquets: tuple[Path, Path]) -> None:
    p1, _ = two_parquets
    with DuckDBEngine() as eng:
        eng.register_file(table_name="customers", parquet_path=p1, row_count=3)
        result = eng.run_sql("WITH c AS (SELECT * FROM customers) SELECT COUNT(*) FROM c")
        assert result.rows[0][0] == 3
