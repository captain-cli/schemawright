from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from schemawright.cli import main
from schemawright.model import parse_migration_filename, format_migration_filename


def write_config(root: Path) -> Path:
    cfg = {
        "projectName": "test-app",
        "defaults": {"orderStep": 10},
        "databases": {
            "app": {
                "driver": "sqlite",
                "database": "runtime/app.sqlite",
                "migrations": {
                    "staged": "sql/migrations/staged/app",
                    "active": "sql/migrations/active/app",
                    "archive": "sql/migrations/archive/app",
                },
                "seeds": {
                    "staged": "sql/seeds/staged/app",
                    "active": "sql/seeds/active/app",
                    "archive": "sql/seeds/archive/app",
                },
                "rollbacks": {
                    "staged": "sql/rollbacks/staged/app",
                    "active": "sql/rollbacks/active/app",
                    "archive": "sql/rollbacks/archive/app",
                },
            }
        },
    }
    path = root / "schemawright.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_parse_new_naming():
    parsed = parse_migration_filename(Path("V0002__0030__add_indexes.sql"))
    assert parsed is not None
    assert parsed.type == "V"
    assert parsed.version == 2
    assert parsed.install_order == 30
    assert parsed.description == "add_indexes"


def test_format_slug():
    assert format_migration_filename("V", 2, 30, "Add User Indexes!") == "V0002__0030__add_user_indexes.sql"


def test_register_creates_staged_file(tmp_path: Path):
    cfg = write_config(tmp_path)
    rc = main(["--config", str(cfg), "--database", "app", "register", "--type", "migration", "--description", "Create Users"])
    assert rc == 0
    assert (tmp_path / "sql/migrations/staged/app/V0001__0010__create_users.sql").exists()


def test_register_explicit_order(tmp_path: Path):
    cfg = write_config(tmp_path)
    rc = main(["--config", str(cfg), "--database", "app", "register", "--type", "migration", "--version", "0002", "--order", "0030", "--description", "add indexes"])
    assert rc == 0
    assert (tmp_path / "sql/migrations/staged/app/V0002__0030__add_indexes.sql").exists()


def test_promote_and_migrate_sqlite(tmp_path: Path):
    cfg = write_config(tmp_path)
    main(["--config", str(cfg), "--database", "app", "register", "--type", "migration", "--description", "create users"])
    migration = tmp_path / "sql/migrations/staged/app/V0001__0010__create_users.sql"
    migration.write_text("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);", encoding="utf-8")
    main(["--config", str(cfg), "--database", "app", "promote", "--version", "0001"])
    assert (tmp_path / "sql/migrations/active/app/V0001__0010__create_users.sql").exists()
    main(["--config", str(cfg), "--database", "app", "migrate", "--no-seeds"])
    conn = sqlite3.connect(tmp_path / "runtime/app.sqlite")
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchall()
    assert rows == [("users",)]
    applied = conn.execute("SELECT type, version, install_order FROM schema_migrations").fetchall()
    assert applied == [("V", 1, 10)]


def test_validate_ok(tmp_path: Path):
    cfg = write_config(tmp_path)
    main(["--config", str(cfg), "--database", "app", "register", "--type", "seed", "--description", "seed roles"])
    assert main(["--config", str(cfg), "--database", "app", "validate"]) == 0


def test_init_set_creates_workspace_with_procedures_and_triggers(tmp_path: Path):
    cfg = write_config(tmp_path)
    rc = main([
        "--config", str(cfg), "--database", "app",
        "init-set", "--name", "User Accounts", "--version", "0003",
        "--migration", "create users",
        "--procedure", "refresh user summary",
        "--trigger", "audit user changes",
        "--seed", "seed roles",
        "--rollback", "drop users",
    ])
    assert rc == 0
    assert (tmp_path / "sql/migrations/staged/app/V0003__user_accounts/V0003__0010__create_users.sql").exists()
    assert (tmp_path / "sql/procedures/staged/app/V0003__user_accounts/P0003__0020__refresh_user_summary.sql").exists()
    assert (tmp_path / "sql/triggers/staged/app/V0003__user_accounts/T0003__0030__audit_user_changes.sql").exists()
    assert (tmp_path / "sql/seeds/staged/app/V0003__user_accounts/S0003__0040__seed_roles.sql").exists()
    assert (tmp_path / "sql/rollbacks/staged/app/V0003__user_accounts/R0003__0050__drop_users.sql").exists()


def test_promote_includes_procedures_and_triggers_by_default(tmp_path: Path):
    cfg = write_config(tmp_path)
    main([
        "--config", str(cfg), "--database", "app",
        "init-set", "--name", "User Accounts", "--version", "0004",
        "--migration", "create users",
        "--procedure", "refresh user summary",
        "--trigger", "audit user changes",
    ])
    assert main(["--config", str(cfg), "--database", "app", "promote", "--version", "0004"]) == 0
    assert (tmp_path / "sql/migrations/active/app/V0004__0010__create_users.sql").exists()
    assert (tmp_path / "sql/procedures/active/app/P0004__0020__refresh_user_summary.sql").exists()
    assert (tmp_path / "sql/triggers/active/app/T0004__0030__audit_user_changes.sql").exists()
