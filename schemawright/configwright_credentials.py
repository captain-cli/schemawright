"""Explicit, offline credential resolution for ConfigWright database definitions.

Secrets are never written back to imported settings or included in exception text.
This module does not create credentials, open database connections, or print values.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import DatabaseConfig

KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
ASSIGNMENT = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\Z")


class CredentialResolutionError(ValueError):
    """Safe-to-display error with no secret material or file contents."""


def _load_secret_file(path: str | Path) -> dict[str, str]:
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CredentialResolutionError("Credential file is unavailable or unreadable") from None
    values: dict[str, str] = {}
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(stripped)
        if not match:
            raise CredentialResolutionError(f"Invalid credential assignment at line {lineno}")
        key, value = match.groups()
        if key in values:
            raise CredentialResolutionError(f"Duplicate credential key: {key}")
        # The file is treated strictly as data; no shell evaluation or ${VAR} interpolation.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        values[key] = value
    return values


def resolve_database_credentials(db: DatabaseConfig, secrets_file: str | Path) -> dict[str, str]:
    """Return a new map of resolved *Ref fields; do not mutate db.settings.

    Caller must never log, serialize, or persist the returned values. Use only for
    a separately authorized database connection. No connection is made here.
    """
    refs = {key: value for key, value in db.settings.items() if key.endswith("Ref")}
    parsed: dict[str, str] = {}
    for field, ref in refs.items():
        if not isinstance(ref, str) or not ref.startswith("env:") or not KEY.fullmatch(ref[4:]):
            raise CredentialResolutionError(f"Invalid credential reference field: {field}")
        parsed[field] = ref[4:]
    if not parsed:
        return {}
    values = _load_secret_file(secrets_file)
    missing = sorted({key for key in parsed.values() if not values.get(key)})
    if missing:
        raise CredentialResolutionError("Missing or empty credential keys: " + ", ".join(missing))
    return {field: values[key] for field, key in parsed.items()}
