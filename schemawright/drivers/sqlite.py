from __future__ import annotations

from pathlib import Path
from typing import Any
import sqlite3
from .base import DatabaseDriver

class SQLiteDriver(DatabaseDriver):
    def __init__(self, settings: dict[str, Any], root: Path):
        super().__init__(settings, root)
        raw = settings.get("database", settings.get("path", ":memory:"))
        self.path = Path(raw)
        if not self.path.is_absolute() and str(self.path) != ":memory:":
            self.path = (root / self.path).resolve()
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def execute_script(self, sql: str) -> None:
        assert self.conn is not None
        self.conn.executescript(sql)
        self.conn.commit()

    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        assert self.conn is not None
        return list(self.conn.execute(sql, params).fetchall())

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        assert self.conn is not None
        self.conn.execute(sql, params)
        self.conn.commit()

    def rebuild(self) -> None:
        self.close()
        if str(self.path) != ":memory:" and self.path.exists():
            self.path.unlink()
        self.connect()
