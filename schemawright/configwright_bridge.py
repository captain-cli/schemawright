"""Read-only contract bridge from ConfigWright's databases.json to SchemaWright.

No DB connection, credential expansion or filesystem writes occur here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SUPPORTED = {"sqlite", "mysql", "postgres", "postgresql", "sqlserver", "oracle", "duckdb"}


def inspect_databases(source: str | Path) -> list[dict[str, Any]]:
    path = Path(source)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("ConfigWright databases.json must contain an object")
    result: list[dict[str, Any]] = []
    for name, settings in sorted(raw.items()):
        if not isinstance(name, str) or not VALID_NAME.fullmatch(name):
            raise ValueError(f"Invalid database identifier: {name!r}")
        if not isinstance(settings, dict):
            raise ValueError(f"Database {name}: settings must be an object")
        driver = settings.get("driver")
        if not isinstance(driver, str) or driver.lower() not in SUPPORTED:
            raise ValueError(f"Database {name}: unsupported or missing driver {driver!r}")
        target = settings.get("database", settings.get("path"))
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"Database {name}: missing database/path target")
        credential_refs = {}
        for key, value in settings.items():
            if key.endswith("Ref"):
                if not isinstance(value, str) or not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", value):
                    raise ValueError(f"Database {name}: {key} must be an env:NAME reference")
                credential_refs[key] = value
        # Explicit secret values are not permitted in ConfigWright's configuration handoff.
        if "password" in settings and settings["password"] not in (None, ""):
            raise ValueError(f"Database {name}: literal password forbidden; use passwordRef")
        result.append({"name": name, "driver": driver.lower(), "target": target,
                       "credentialRefs": credential_refs})
    return result
