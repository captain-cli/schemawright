from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
from typing import Any

ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

@dataclass
class PathSet:
    staged: Path
    active: Path
    archive: Path

@dataclass
class DatabaseConfig:
    name: str
    driver: str
    settings: dict[str, Any]
    migrations: PathSet
    seeds: PathSet
    rollbacks: PathSet
    procedures: PathSet
    triggers: PathSet

@dataclass
class SchemawrightConfig:
    project_name: str
    defaults: dict[str, Any]
    databases: dict[str, DatabaseConfig]
    config_path: Path


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return ENV_RE.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    return value


def _pathset(raw: Any, default_base: str, db_name: str, root: Path) -> PathSet:
    if isinstance(raw, str):
        active = Path(raw)
        staged = active.parent.parent / "staged" / db_name if active.parent.name == db_name else Path(default_base) / "staged" / db_name
        archive = active.parent.parent / "archive" / db_name if active.parent.name == db_name else Path(default_base) / "archive" / db_name
    elif isinstance(raw, dict):
        staged = Path(raw.get("staged", f"{default_base}/staged/{db_name}"))
        active = Path(raw.get("active", f"{default_base}/active/{db_name}"))
        archive = Path(raw.get("archive", f"{default_base}/archive/{db_name}"))
    else:
        staged = Path(f"{default_base}/staged/{db_name}")
        active = Path(f"{default_base}/active/{db_name}")
        archive = Path(f"{default_base}/archive/{db_name}")
    return PathSet(_resolve(root, staged), _resolve(root, active), _resolve(root, archive))


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else (root / value).resolve()


def load_config(path: str | Path, root: str | Path | None = None, *, canonical: bool = False) -> SchemawrightConfig:
    config_path = Path(path).resolve()
    if canonical:
        from captain_core.project_manifest import resolve_tool_config

        project_root = Path(root).resolve() if root else (config_path if config_path.is_dir() else config_path.parent)
        resolved = resolve_tool_config("schemawright", start=project_root, required=True)
        assert resolved is not None
        config_path = resolved.path
        config_root = project_root
        raw = resolved.config
    else:
        config_root = Path(root).resolve() if root else config_path.parent
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw = expand_env(raw)
    defaults = raw.get("defaults", {})
    dbs: dict[str, DatabaseConfig] = {}
    for name, db_raw in raw.get("databases", {}).items():
        db_raw = dict(db_raw)
        driver = db_raw.get("driver", "sqlite")
        migrations = _pathset(db_raw.get("migrations"), "sql/migrations", name, config_root)
        seeds = _pathset(db_raw.get("seeds"), "sql/seeds", name, config_root)
        rollbacks = _pathset(db_raw.get("rollbacks"), "sql/rollbacks", name, config_root)
        procedures = _pathset(db_raw.get("procedures"), "sql/procedures", name, config_root)
        triggers = _pathset(db_raw.get("triggers"), "sql/triggers", name, config_root)
        dbs[name] = DatabaseConfig(name, driver, db_raw, migrations, seeds, rollbacks, procedures, triggers)
    return SchemawrightConfig(
        project_name=raw.get("projectName", raw.get("project", config_path.parent.name)),
        defaults=defaults,
        databases=dbs,
        config_path=config_path,
    )


def write_starter_config(path: Path, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Config already exists: {path}. Use --force to overwrite.")
    path.write_text("""{
  "projectName": "example-app",
  "defaults": {
    "orderStep": 10,
    "stageByDefault": true
  },
  "databases": {
    "app": {
      "driver": "sqlite",
      "database": ".captain/runtime/db/app.sqlite",
      "migrations": {
        "staged": "sql/migrations/staged/app",
        "active": "sql/migrations/active/app",
        "archive": "sql/migrations/archive/app"
      },
      "seeds": {
        "staged": "sql/seeds/staged/app",
        "active": "sql/seeds/active/app",
        "archive": "sql/seeds/archive/app"
      },
      "rollbacks": {
        "staged": "sql/rollbacks/staged/app",
        "active": "sql/rollbacks/active/app",
        "archive": "sql/rollbacks/archive/app"
      },
      "procedures": {
        "staged": "sql/procedures/staged/app",
        "active": "sql/procedures/active/app",
        "archive": "sql/procedures/archive/app"
      },
      "triggers": {
        "staged": "sql/triggers/staged/app",
        "active": "sql/triggers/active/app",
        "archive": "sql/triggers/archive/app"
      }
    }
  }
}
""", encoding="utf-8")
