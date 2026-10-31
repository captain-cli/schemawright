"""Read-only ConfigWright -> SchemaWright configuration import.

No environment interpolation, secret lookup, SQL, or filesystem writes.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import DatabaseConfig, SchemawrightConfig, _pathset
from .configwright_bridge import inspect_databases


def import_configwright(source: str | Path, *, root: str | Path, database: str | None = None) -> SchemawrightConfig:
    source_path = Path(source).resolve()
    project_root = Path(root).resolve()
    definitions = inspect_databases(source_path)
    if not definitions:
        raise ValueError("ConfigWright database configuration contains no databases")
    raw = json.loads(source_path.read_text(encoding="utf-8"))
    names = {db["name"] for db in definitions}
    if database is not None and database not in names:
        raise ValueError(f"Unknown ConfigWright database: {database}")

    databases: dict[str, DatabaseConfig] = {}
    for item in definitions:
        name = item["name"]
        if database is not None and database != name:
            continue
        settings = dict(raw[name])
        # Preserve env:NAME references literally; never resolve credentials at import time.
        driver = item["driver"]
        databases[name] = DatabaseConfig(
            name=name,
            driver=driver,
            settings=settings,
            migrations=_pathset(settings.get("migrations"), "sql/migrations", name, project_root),
            seeds=_pathset(settings.get("seeds"), "sql/seeds", name, project_root),
            rollbacks=_pathset(settings.get("rollbacks"), "sql/rollbacks", name, project_root),
            procedures=_pathset(settings.get("procedures"), "sql/procedures", name, project_root),
            triggers=_pathset(settings.get("triggers"), "sql/triggers", name, project_root),
        )
    return SchemawrightConfig(project_name=project_root.name, defaults={}, databases=databases, config_path=source_path)
