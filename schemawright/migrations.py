from __future__ import annotations

from pathlib import Path

from .files import list_sql_files
from .model import MigrationFile


def normalize_description(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip()


def discover_sql_files(directory: str | Path, kind: str) -> list[MigrationFile]:
    return [mf for mf in list_sql_files(Path(directory)) if mf.type == kind.upper()]


def read_sql_file(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8").strip()
