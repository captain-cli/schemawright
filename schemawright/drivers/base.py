from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

class DatabaseDriver(ABC):
    def __init__(self, settings: dict[str, Any], root: Path):
        self.settings = settings
        self.root = root

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def execute_script(self, sql: str) -> None: ...

    @abstractmethod
    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]: ...

    @abstractmethod
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None: ...

    def rebuild(self) -> None:
        raise NotImplementedError("rebuild is not implemented for this driver")

    def repair(self) -> None:
        rows = self.query_all("SELECT name FROM sqlite_master WHERE type='table'")
        for (name,) in rows:
            self.execute(f'DROP TABLE IF EXISTS "{name}"')
