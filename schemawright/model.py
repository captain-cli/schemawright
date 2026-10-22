from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

MIGRATION_RE = re.compile(
    r"^(?P<prefix>[VSRPT])(?P<version>\d{4})__(?P<order>\d{4})__(?P<description>[a-z0-9_\-]+)\.sql$"
)

TYPE_ALIASES = {
    "v": "V", "version": "V", "migration": "V", "migrate": "V",
    "s": "S", "seed": "S", "seeds": "S",
    "r": "R", "rollback": "R", "down": "R",
    "p": "P", "proc": "P", "procedure": "P", "procedures": "P", "stored-procedure": "P", "stored_procedure": "P",
    "t": "T", "trigger": "T", "triggers": "T",
}

@dataclass(frozen=True, order=True)
class MigrationFile:
    type: str
    version: int
    install_order: int
    description: str
    path: Path
    filename: str

    @property
    def version_text(self) -> str:
        return f"{self.version:04d}"

    @property
    def order_text(self) -> str:
        return f"{self.install_order:04d}"


def normalize_type(value: str) -> str:
    key = value.strip().lower()
    if key not in TYPE_ALIASES:
        raise ValueError(f"Unknown migration type '{value}'. Expected migration, seed, rollback, procedure, or trigger.")
    return TYPE_ALIASES[key]


def prefix_to_collection(prefix: str) -> str:
    if prefix == "V":
        return "migrations"
    if prefix == "S":
        return "seeds"
    if prefix == "R":
        return "rollbacks"
    if prefix == "P":
        return "procedures"
    if prefix == "T":
        return "triggers"
    raise ValueError(f"Unsupported prefix: {prefix}")


def slugify_description(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:80] or "migration"


def parse_migration_filename(path: Path) -> MigrationFile | None:
    match = MIGRATION_RE.match(path.name)
    if not match:
        return None
    return MigrationFile(
        type=match.group("prefix"),
        version=int(match.group("version")),
        install_order=int(match.group("order")),
        description=match.group("description"),
        path=path,
        filename=path.name,
    )


def format_migration_filename(prefix: str, version: int, install_order: int, description: str) -> str:
    return f"{prefix}{version:04d}__{install_order:04d}__{slugify_description(description)}.sql"
