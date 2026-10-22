from __future__ import annotations

import argparse
from pathlib import Path
import sys
from . import __version__
from .config import load_config, write_starter_config
from .files import ensure_dirs, next_version_and_order, register_file, promote_version, init_migration_set
from .model import normalize_type
from .runner import MigrationRunner, RunOptions
from .validate import validate_database


def parse_version(value: str | None) -> int | None:
    if not value:
        return None
    return int(value)


def parse_database_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {x.strip() for x in value.split(",") if x.strip()}


def selected_databases(config, csv: str | None):
    wanted = parse_database_csv(csv)
    if not wanted:
        return list(config.databases.values())
    missing = wanted - set(config.databases)
    if missing:
        raise SystemExit(f"Unknown database(s): {', '.join(sorted(missing))}")
    return [config.databases[name] for name in sorted(wanted)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="schemawright", description="Project-agnostic SQL migration lifecycle tool.")
    parser.add_argument("--version", action="version", version=f"schemawright {__version__}")
    parser.add_argument("--config", default="schemawright.json", help="Path to schemawright.json.")
    parser.add_argument("--canonical", action="store_true", help="Resolve Schemawright from the Captain project manifest.json.")
    parser.add_argument("--root", default=None, help="Project root for resolving relative config paths. Defaults to config directory.")
    parser.add_argument("--database", "--db", default="", help="Comma-separated database names.")
    parser.add_argument("--dry-run", action="store_true", help="Plan without writing/executing where supported.")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Create a starter schemawright.json.")
    init.add_argument("--force", action="store_true")

    reg = sub.add_parser("register", help="Register/create a staged migration, seed, or rollback SQL file.")
    reg.add_argument("--type", default="migration", help="migration, seed, or rollback.")
    reg.add_argument("--description", required=True)
    reg.add_argument("--version")
    reg.add_argument("--order")
    reg.add_argument("--next-version", action="store_true")
    reg.add_argument("--source")
    reg.add_argument("--move", action="store_true")
    reg.add_argument("--active", action="store_true", help="Write directly to active instead of staged.")
    reg.add_argument("--overwrite", action="store_true")

    init_set = sub.add_parser("init-set", help="Initialize a staged migration-set workspace for a larger database change.")
    init_set.add_argument("--name", required=True, help="Human-readable migration-set name.")
    init_set.add_argument("--version", help="Schema/release version for the set. Defaults to the next migration version.")
    init_set.add_argument("--next-version", action="store_true", help="Force the next schema/release version.")
    init_set.add_argument("--migration", action="append", default=[], help="Starter migration description. Repeatable.")
    init_set.add_argument("--procedure", action="append", default=[], help="Starter stored procedure description. Repeatable.")
    init_set.add_argument("--trigger", action="append", default=[], help="Starter trigger description. Repeatable.")
    init_set.add_argument("--seed", action="append", default=[], help="Starter seed description. Repeatable.")
    init_set.add_argument("--rollback", action="append", default=[], help="Starter rollback description. Repeatable.")
    init_set.add_argument("--order-start", type=int, default=None, help="Starting install order. Defaults to orderStep.")
    init_set.add_argument("--overwrite", action="store_true")

    prom = sub.add_parser("promote", help="Promote staged files for a version into active.")
    prom.add_argument("--version", required=True)
    prom.add_argument("--move", action="store_true")
    prom.add_argument("--include-seeds", action="store_true")
    prom.add_argument("--include-rollbacks", action="store_true")
    prom.add_argument("--skip-procedures", action="store_true", help="Do not promote stored procedure files.")
    prom.add_argument("--skip-triggers", action="store_true", help="Do not promote trigger files.")
    prom.add_argument("--dry-run", action="store_true", help="Preview promotion without copying files.")

    sub.add_parser("validate", help="Validate migration file naming and duplicate keys.")
    sub.add_parser("status", help="Show applied/pending active migrations.")
    mig = sub.add_parser("migrate", help="Apply active migrations and seeds.")
    mig.add_argument("--no-seeds", action="store_true")
    mig.add_argument("--dry-run", action="store_true", help="Preview migration without executing SQL.")
    repair = sub.add_parser("repair", help="Drop tables where supported, then migrate.")
    repair.add_argument("--dry-run", action="store_true")
    rebuild = sub.add_parser("rebuild", help="Rebuild database where supported, then migrate.")
    rebuild.add_argument("--dry-run", action="store_true")
    rb = sub.add_parser("rollback", help="Plan rollback files needed to reach a version.")
    rb.add_argument("--to-version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "status"
    config_path = Path(args.config)

    if command == "init":
        write_starter_config(config_path, force=args.force)
        print(f"Created: {config_path}")
        return 0

    config = load_config(config_path, root=args.root, canonical=args.canonical)
    dbs = selected_databases(config, args.database)

    if command == "register":
        if len(dbs) != 1:
            raise SystemExit("register requires exactly one --database target")
        db = dbs[0]
        ensure_dirs(db)
        prefix = normalize_type(args.type)
        order_step = int(config.defaults.get("orderStep", 10))
        version = parse_version(args.version)
        if args.order:
            install_order = int(args.order)
            if version is None:
                known_version, _ = next_version_and_order(db, prefix, order_step, None, args.next_version)
                version = known_version
        else:
            version, install_order = next_version_and_order(db, prefix, order_step, version, args.next_version)
        target = register_file(
            db,
            prefix,
            args.description,
            version,
            install_order,
            source=Path(args.source) if args.source else None,
            move=args.move,
            target_stage="active" if args.active else "staged",
            overwrite=args.overwrite,
        )
        print(f"Registered: {target}")
        return 0

    if command == "init-set":
        if len(dbs) != 1:
            raise SystemExit("init-set requires exactly one --database target")
        db = dbs[0]
        ensure_dirs(db)
        order_step = int(config.defaults.get("orderStep", 10))
        version = parse_version(args.version)
        if version is None:
            version, _ = next_version_and_order(db, "V", order_step, None, args.next_version or True)
        entries = {
            "V": args.migration,
            "P": args.procedure,
            "T": args.trigger,
            "S": args.seed,
            "R": args.rollback,
        }
        created = init_migration_set(
            db,
            version,
            args.name,
            entries,
            order_step,
            order_start=args.order_start,
            overwrite=args.overwrite,
        )
        for path in created:
            print(f"Created: {path}")
        return 0

    if command == "promote":
        prefixes = ["V"]
        if not args.skip_procedures:
            prefixes.append("P")
        if not args.skip_triggers:
            prefixes.append("T")
        if args.include_seeds:
            prefixes.append("S")
        if args.include_rollbacks:
            prefixes.append("R")
        for db in dbs:
            planned = promote_version(db, int(args.version), prefixes, move=args.move, dry_run=args.dry_run)
            for src, dest in planned:
                print(("Would promote" if args.dry_run else "Promoted") + f": {src} -> {dest}")
        return 0

    if command == "validate":
        had_errors = False
        for db in dbs:
            issues = validate_database(db)
            if not issues:
                print(f"{db.name}: OK")
            for issue in issues:
                print(f"{db.name}: {issue.level}: {issue.message}")
                had_errors = had_errors or issue.level == "error"
        return 1 if had_errors else 0

    if command == "rollback":
        to_version = int(args.to_version)
        for db in dbs:
            files = [f for f in sorted(__import__('schemawright.files').files.list_active(db, 'R'), key=lambda m: (m.version, m.install_order), reverse=True) if f.version > to_version]
            for file in files:
                print(f"would rollback {file.filename}")
        return 0

    for db in dbs:
        runner = MigrationRunner(db, RunOptions(root=Path(args.root).resolve() if args.root else Path(args.config).resolve().parent, dry_run=args.dry_run, include_seeds=not getattr(args, "no_seeds", False)))
        if command == "status":
            for state, mf in runner.status():
                print(f"{db.name}: {state:<7} {mf.filename}")
        elif command == "migrate":
            runner.migrate()
        elif command == "repair":
            runner.repair()
        elif command == "rebuild":
            runner.rebuild()
        else:
            parser.error(f"Unknown command: {command}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
