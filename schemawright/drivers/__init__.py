from __future__ import annotations

from pathlib import Path
from typing import Any
from .base import DatabaseDriver
from .sqlite import SQLiteDriver

class MissingDriver(DatabaseDriver):
    package_name = ""
    def connect(self) -> None:
        raise RuntimeError(f"Driver '{self.settings.get('driver')}' requires optional dependency: {self.package_name}")
    def close(self) -> None: pass
    def execute_script(self, sql: str) -> None: self.connect()
    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]: self.connect(); return []
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None: self.connect()


def create_driver(settings: dict[str, Any], root: Path) -> DatabaseDriver:
    key = str(settings.get("driver", "sqlite")).lower()
    if key in {"sqlite", "sqlite3"}:
        return SQLiteDriver(settings, root)
    if key in {"mysql", "mariadb"}:
        try:
            from .mysql import MySQLDriver
            return MySQLDriver(settings, root)
        except Exception as exc:
            raise RuntimeError("Install schemawright[mysql] for MySQL/MariaDB support") from exc
    if key in {"postgres", "postgresql", "cockroach", "cockroachdb"}:
        try:
            from .postgres import PostgresDriver
            return PostgresDriver(settings, root)
        except Exception as exc:
            raise RuntimeError("Install schemawright[postgres] for PostgreSQL/CockroachDB support") from exc
    if key in {"sqlserver", "mssql"}:
        try:
            from .sqlserver import SQLServerDriver
            return SQLServerDriver(settings, root)
        except Exception as exc:
            raise RuntimeError("Install schemawright[sqlserver] for Microsoft SQL Server support") from exc
    if key == "duckdb":
        try:
            from .duckdb import DuckDBDriver
            return DuckDBDriver(settings, root)
        except Exception as exc:
            raise RuntimeError("Install schemawright[duckdb] for DuckDB support") from exc
    if key == "oracle":
        try:
            from .oracle import OracleDriver
            return OracleDriver(settings, root)
        except Exception as exc:
            raise RuntimeError("Install schemawright[oracle] for Oracle support") from exc
    raise ValueError(f"Unsupported driver: {key}")
