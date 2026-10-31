"""Read-only offline migration inventory for imported ConfigWright definitions.

This is NOT a live pending/applied migration calculation: database history is not read.
"""
from __future__ import annotations

from pathlib import Path

from .config import DatabaseConfig
from .files import list_sql_files
from .model import parse_migration_filename

COLLECTIONS = (("migration", "V", "migrations"), ("procedure", "P", "procedures"),
               ("trigger", "T", "triggers"), ("seed", "S", "seeds"),
               ("rollback", "R", "rollbacks"))


def plan_database(db: DatabaseConfig) -> tuple[list[dict], list[str]]:
    """Inventory SQL files and flag unsafe or ambiguous entries without mutating files."""
    entries: list[dict] = []
    issues: list[str] = []
    for kind, prefix, collection in COLLECTIONS:
        paths = getattr(db, collection)
        for stage in ("active", "staged"):
            directory = getattr(paths, stage)
            if not directory.is_dir():
                continue  # An empty/new project can have no migration directory yet.
            seen: dict[tuple[int, int], Path] = {}
            for file in sorted(directory.rglob("*.sql")):
                if not file.is_file():
                    continue
                parsed = parse_migration_filename(file)
                if parsed is None:
                    issues.append(f"{stage}/{collection}: invalid SQL filename: {file}")
                    continue
                if parsed.type != prefix:
                    issues.append(f"{stage}/{collection}: unexpected prefix {parsed.type}: {file}")
                    continue
                key = (parsed.version, parsed.install_order)
                if key in seen:
                    issues.append(f"{stage}/{collection}: duplicate version/order {key}: {seen[key]} and {file}")
                    continue
                seen[key] = file
                entries.append({"kind": kind, "stage": stage, "version": parsed.version,
                                "order": parsed.install_order, "file": str(file)})
    entries.sort(key=lambda item: (item["version"], item["order"],
                                   item["stage"] != "active", item["kind"], item["file"]))
    return entries, issues
