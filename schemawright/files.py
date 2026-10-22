from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
from .config import DatabaseConfig
from .model import MigrationFile, parse_migration_filename, prefix_to_collection, format_migration_filename, slugify_description


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def list_sql_files(path: Path) -> list[MigrationFile]:
    if not path.exists():
        return []
    files: list[MigrationFile] = []
    for item in path.rglob("*.sql"):
        if item.is_file():
            parsed = parse_migration_filename(item)
            if parsed:
                files.append(parsed)
    return sorted(files, key=lambda m: (m.version, m.install_order, m.filename))


def list_all_known(db: DatabaseConfig, prefix: str) -> list[MigrationFile]:
    collection = prefix_to_collection(prefix)
    paths = getattr(db, collection)
    return sorted(
        list_sql_files(paths.staged) + list_sql_files(paths.active) + list_sql_files(paths.archive),
        key=lambda m: (m.version, m.install_order, m.filename),
    )


def list_active(db: DatabaseConfig, prefix: str) -> list[MigrationFile]:
    collection = prefix_to_collection(prefix)
    paths = getattr(db, collection)
    return list_sql_files(paths.active)


def ensure_dirs(db: DatabaseConfig) -> None:
    for pathset in (db.migrations, db.seeds, db.rollbacks, db.procedures, db.triggers):
        pathset.staged.mkdir(parents=True, exist_ok=True)
        pathset.active.mkdir(parents=True, exist_ok=True)
        pathset.archive.mkdir(parents=True, exist_ok=True)


def next_version_and_order(db: DatabaseConfig, prefix: str, order_step: int, version: int | None, next_version: bool) -> tuple[int, int]:
    known = list_all_known(db, prefix)
    max_version = max([m.version for m in known], default=0)
    if version is None:
        version = max_version + 1 if next_version or max_version == 0 else max_version
    if next_version and version <= max_version:
        version = max_version + 1
    orders = [m.install_order for m in known if m.version == version]
    next_order = (max(orders) + order_step) if orders else order_step
    return version, next_order


def register_file(
    db: DatabaseConfig,
    prefix: str,
    description: str,
    version: int,
    install_order: int,
    source: Path | None = None,
    move: bool = False,
    target_stage: str = "staged",
    overwrite: bool = False,
) -> Path:
    collection = prefix_to_collection(prefix)
    pathset = getattr(db, collection)
    target_dir = getattr(pathset, target_stage)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / format_migration_filename(prefix, version, install_order, description)
    if target.exists() and not overwrite:
        raise FileExistsError(f"Target already exists: {target}")
    if source:
        if move:
            shutil.move(str(source), str(target))
        else:
            shutil.copyfile(source, target)
    else:
        label = {"V": "migration", "S": "seed", "R": "rollback", "P": "procedure", "T": "trigger"}[prefix]
        target.write_text(
            f"-- Schemawright {label}\n"
            f"-- Type: {prefix}\n"
            f"-- Version: {version:04d}\n"
            f"-- Install Order: {install_order:04d}\n"
            f"-- Description: {description}\n\n"
            f"-- Write SQL below this line.\n",
            encoding="utf-8",
        )
    return target


def promote_version(db: DatabaseConfig, version: int, prefixes: list[str], move: bool = False, dry_run: bool = False) -> list[tuple[Path, Path]]:
    ensure_dirs(db)
    planned: list[tuple[Path, Path]] = []
    for prefix in prefixes:
        collection = prefix_to_collection(prefix)
        pathset = getattr(db, collection)
        for mf in list_sql_files(pathset.staged):
            if mf.version == version:
                dest = pathset.active / mf.filename
                planned.append((mf.path, dest))
    if not dry_run:
        for src, dest in planned:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                raise FileExistsError(f"Promotion target already exists: {dest}")
            if move:
                shutil.move(str(src), str(dest))
            else:
                shutil.copyfile(src, dest)
    return planned


def init_migration_set(
    db: DatabaseConfig,
    version: int,
    name: str,
    entries: dict[str, list[str]],
    order_step: int,
    order_start: int | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """Create a staged version workspace with optional starter SQL files.

    The workspace layout is nested under each staged collection, for example:
    sql/migrations/staged/app/V0003/V0003__0010__create_users.sql
    sql/procedures/staged/app/V0003/P0003__0020__refresh_user_summary.sql
    """
    ensure_dirs(db)
    created: list[Path] = []
    workspace_name = f"V{version:04d}__{slugify_description(name)}"
    order = order_start if order_start is not None else order_step

    prefix_order = ["V", "P", "T", "S", "R"]
    for prefix in prefix_order:
        descriptions = entries.get(prefix, [])
        collection = prefix_to_collection(prefix)
        pathset = getattr(db, collection)
        workspace_dir = pathset.staged / workspace_name
        workspace_dir.mkdir(parents=True, exist_ok=True)
        readme = workspace_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# Schemawright migration set {workspace_name}\n\n"
                f"Database: `{db.name}`\n\n"
                f"This staged workspace can contain `{prefix}` SQL files for version `{version:04d}`.\n",
                encoding="utf-8",
            )
            created.append(readme)
        for description in descriptions:
            target = workspace_dir / format_migration_filename(prefix, version, order, description)
            if target.exists() and not overwrite:
                raise FileExistsError(f"Target already exists: {target}")
            label = {"V": "migration", "S": "seed", "R": "rollback", "P": "procedure", "T": "trigger"}[prefix]
            target.write_text(
                f"-- Schemawright {label}\n"
                f"-- Type: {prefix}\n"
                f"-- Version: {version:04d}\n"
                f"-- Install Order: {order:04d}\n"
                f"-- Description: {description}\n\n"
                f"-- Write SQL below this line.\n",
                encoding="utf-8",
            )
            created.append(target)
            order += order_step
    return created
