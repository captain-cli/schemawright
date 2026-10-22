from __future__ import annotations
from pathlib import Path
from typing import Any
from .base import DatabaseDriver

class MySQLDriver(DatabaseDriver):
    def connect(self) -> None: raise RuntimeError("MySQL/MariaDB adapter stub: install dependency and complete adapter wiring.")
    def close(self) -> None: pass
    def execute_script(self, sql: str) -> None: self.connect()
    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]: self.connect(); return []
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None: self.connect()

PostgresDriver = MySQLDriver
SQLServerDriver = MySQLDriver
DuckDBDriver = MySQLDriver
OracleDriver = MySQLDriver
