from __future__ import annotations
from dataclasses import dataclass
from .config import DatabaseConfig
from .files import list_sql_files

@dataclass
class ValidationIssue:
    level: str
    message: str


def validate_database(db: DatabaseConfig) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: dict[tuple[str, int, int], str] = {}
    for prefix, pathset in (("V", db.migrations), ("S", db.seeds), ("R", db.rollbacks)):
        for lane_name, lane_path in (("staged", pathset.staged), ("active", pathset.active), ("archive", pathset.archive)):
            if not lane_path.exists():
                continue
            for file in lane_path.iterdir():
                if file.is_file() and file.suffix == ".sql":
                    parsed = list_sql_files(lane_path)
                    break
            for mf in list_sql_files(lane_path):
                if mf.type != prefix:
                    issues.append(ValidationIssue("error", f"{mf.filename} is in {prefix} lane but has prefix {mf.type}"))
                key = (mf.type, mf.version, mf.install_order)
                if key in seen:
                    issues.append(ValidationIssue("error", f"duplicate migration key {key}: {seen[key]} and {mf.path}"))
                seen[key] = str(mf.path)
    return issues
